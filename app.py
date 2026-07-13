from __future__ import annotations

import json
import base64
import html
import inspect
import mimetypes
import os
import random
import secrets
import sys
import threading
import traceback
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime
from functools import lru_cache
from pathlib import Path
from typing import Any
from urllib.parse import quote
from urllib.request import Request, urlopen

import pandas as pd
import streamlit as st
import streamlit.components.v1 as components
import bangumi_client as bgm
import database as db
import daily_art
import filtering as flt
import scoring
import seasonal_service as seasonal
import ui_settings as ui_cfg

from ui_components import (
    cover_for, diff_label, fmt_score, inject_css, ranking_list, ranking_showcase, render_empty_state,
    render_category_overview, render_page_shell, render_profile_summary,
    render_score_distribution, render_section_heading,
    render_season_time_windows, render_top_nav, work_grid_card, work_row,
)

ROOT = Path(__file__).resolve().parent
LOG_DIR = ROOT / "logs"
ERROR_LOG_PATH = LOG_DIR / "app_errors.log"
BANGUMI_RANK_POSTER_DIR = ROOT / "static" / "bangumi_rank_posters"
BANGUMI_IMAGE_HEADERS = {
    "User-Agent": "Yang-gumi/1.0 (+local Streamlit Bangumi ranking poster cache)",
    "Referer": "https://bgm.tv/",
}
# Kept only as a migration marker for older validation; it is no longer rendered.
LEGACY_BIND_ACTION_LABEL = "使用此数据"
TYPES = ["动画", "漫画", "轻小说", "游戏", "其他"]
SEARCH_CATEGORIES = list(bgm.CATEGORY_LABELS)
SUBTYPES = ["TV", "剧场版", "OVA", "WEB", "SP", "漫画", "轻小说", "Galgame", "主机游戏", "PC游戏", "手游", "其他"]
STATUSES = ["想看", "在看", "已看", "搁置", "弃置", "想重看", "重看中"]
TAG_CATEGORIES = ["氛围", "题材", "观感", "角色类型", "个人偏好", "时代", "其他"]
SCORE_LABELS = {
    "score_total": "总评分", "score_story": "剧情", "score_character": "角色塑造",
    "score_art": "作画 / 摄影", "score_direction": "演出", "score_music": "音乐 / 配音", "score_atmosphere": "氛围感",
    "score_aftertaste": "情绪后劲", "score_uniqueness": "独特性", "score_personal": "个人偏爱",
    "score_pacing": "节奏",
    "score_influence": "影响力", "score_originality": "开创性",
    "score_imbalance_penalty": "偏科惩罚",
    "rewatch_value": "重看 / 重玩价值",
}
COMPONENT_SCORE_FIELDS = scoring.COMPONENT_SCORE_FIELDS
RANK_METRICS = {
    "我的总评分": "score_total", "个人偏爱": "score_personal", "重看 / 重玩价值": "rewatch_value",
    "氛围感": "score_atmosphere", "情绪后劲": "score_aftertaste", "角色塑造": "score_character",
    "作画 / 摄影": "score_art", "演出": "score_direction", "音乐 / 配音": "score_music", "节奏": "score_pacing",
    "剧情": "score_story", "独特性": "score_uniqueness",
    "影响力": "score_influence", "开创性": "score_originality",
    "Bangumi 评分人数": "bangumi_total_votes", "Bangumi 排名": "bangumi_rank",
    "Bangumi 公共评分": "bangumi_score", "我比 Bangumi 高最多": "score_diff",
    "我比 Bangumi 低最多": "score_diff_asc", "最近完成": "finish_date", "最近添加": "created_at",
    "Bangumi 高分但我个人无感": "special_public_high", "Bangumi 一般但我很喜欢": "special_mine_high",
}
LIBRARY_SORTS = {
    "我的总评分从高到低": ("score_total", True), "我的总评分从低到高": ("score_total", False),
    "Bangumi 评分从高到低": ("bangumi_score", True), "Bangumi 评分从低到高": ("bangumi_score", False),
    "Bangumi 排名从高到低": ("bangumi_rank", False), "Bangumi 排名从低到高": ("bangumi_rank", True),
    "个人偏爱从高到低": ("score_personal", True), "重看 / 重玩价值从高到低": ("rewatch_value", True),
    "氛围感从高到低": ("score_atmosphere", True), "情绪后劲从高到低": ("score_aftertaste", True),
    "演出从高到低": ("score_direction", True),
    "评分差值从高到低": ("score_diff", True), "评分差值从低到高": ("score_diff", False),
    "最近添加": ("created_at", True), "最近完成": ("finish_date", True),
    "年份从新到旧": ("year", True), "年份从旧到新": ("year", False),
}
SCORE_INTERVALS = flt.get_score_ranges()
DIFF_INTERVALS = flt.DIFF_ABS_RANGES
READ_ONLY_MODE = os.getenv("YANGGUMI_READ_ONLY", "0") == "1"
SHARE_TOKEN = os.getenv("YANGGUMI_SHARE_TOKEN", "")


@lru_cache(maxsize=1)
def _running_under_streamlit_apptest() -> bool:
    return "streamlit.testing.v1" in sys.modules or any(
        "streamlit\\testing" in frame.filename or "streamlit/testing" in frame.filename
        for frame in inspect.stack()
    )


def _score_label(field: str, config: dict[str, Any] | None = None) -> str:
    if str(field).startswith("custom_"):
        return scoring.score_label(field, config)
    return SCORE_LABELS.get(field, scoring.score_label(field, config))


def _custom_scores(work: dict[str, Any]) -> dict[str, float]:
    raw = work.get("custom_scores_json")
    try:
        parsed = json.loads(raw or "{}")
    except (TypeError, json.JSONDecodeError):
        return {}
    if not isinstance(parsed, dict):
        return {}
    result: dict[str, float] = {}
    for key, value in parsed.items():
        if not str(key).startswith("custom_") or value in (None, ""):
            continue
        try:
            result[str(key)] = float(value)
        except (TypeError, ValueError):
            continue
    return result


def _work_score_value(work: dict[str, Any], field: str) -> Any:
    if str(field).startswith("custom_"):
        return _custom_scores(work).get(field)
    return work.get(field)


def _rank_metric_map(config: dict[str, Any] | None = None) -> dict[str, str]:
    mapping = dict(RANK_METRICS)
    active_config = config or scoring.load_score_config()
    for group_key in scoring.SCORE_GROUPS:
        for field, label in scoring.score_labels(group_key, active_config).items():
            mapping[label] = field
    return mapping


def _log_app_error(page_name: str, exc: Exception) -> str:
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    error_id = datetime.now().strftime("%Y%m%d-%H%M%S-%f")
    payload = {
        "id": error_id,
        "time": datetime.now().isoformat(timespec="seconds"),
        "page": page_name,
        "type": type(exc).__name__,
        "message": str(exc),
        "traceback": traceback.format_exc(),
    }
    with ERROR_LOG_PATH.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(payload, ensure_ascii=False) + "\n")
    return error_id


def _assert_runtime_contracts() -> None:
    contracts = {
        "daily_art": (daily_art, ("load_manifest", "refresh_manifest_async_if_needed", "browser_candidates")),
        "scoring": (scoring, ("calculate_total_score", "imbalance_penalty_cap", "explain_score_breakdown")),
        "seasonal_service": (seasonal, ("is_homepage_seasonal_anime", "is_tv_seasonal_anime")),
        "bangumi_client": (bgm, ("ranked_browser_subjects", "clear_ranking_cache")),
    }
    missing = [
        f"{module_name}.{attr}"
        for module_name, (module, attrs) in contracts.items()
        for attr in attrs
        if not hasattr(module, attr)
    ]
    if missing:
        raise RuntimeError("运行模块版本不一致：" + "、".join(missing))


def render_page_safely(page_name: str) -> None:
    try:
        _assert_runtime_contracts()
        PAGES[page_name]()
    except Exception as exc:
        error_id = _log_app_error(page_name, exc)
        st.markdown('<div class="yg-home-kicker">YANG-GUMI / RECOVERY</div>', unsafe_allow_html=True)
        st.title("页面暂时不可用")
        st.error("这个页面刚才遇到错误，但网站没有崩溃；你可以继续切换到其他页面。")
        st.caption(f"错误编号：{error_id} · 已记录到 logs/app_errors.log")
        with st.expander("查看简要错误", expanded=False):
            st.code(f"{type(exc).__name__}: {exc}", language="text")


st.set_page_config(page_title="Yang-gumi", page_icon="🌸", layout="wide", initial_sidebar_state="collapsed")
if READ_ONLY_MODE and SHARE_TOKEN:
    supplied_token = str(st.query_params.get("access") or "")
    if not secrets.compare_digest(supplied_token, SHARE_TOKEN):
        st.error("这个只读分享链接无效或已经更换。")
        st.stop()
db.init_db()
if not READ_ONLY_MODE and not _running_under_streamlit_apptest():
    seasonal.start_midnight_refresh_scheduler()
APP_SETTINGS = ui_cfg.load_settings()
inject_css(APP_SETTINGS)


def _live_data_revision() -> str:
    """Return a cheap marker that changes whenever a local work is saved."""
    with db.connect() as connection:
        work_row = connection.execute(
            "SELECT COALESCE(MAX(updated_at), ''), COUNT(*) FROM works"
        ).fetchone()
        tag_count = connection.execute("SELECT COUNT(*) FROM work_tags").fetchone()[0]
    return f"{work_row[0]}:{work_row[1]}:{tag_count}"


def _seasonal_data_revision(year: int, season_code: str) -> str:
    with db.connect() as connection:
        row = connection.execute(
            """
            SELECT COALESCE(MAX(updated_at), ''), COUNT(*)
            FROM seasonal_anime_cache
            WHERE season_year=? AND season_code=?
            """,
            (int(year), str(season_code)),
        ).fetchone()
    return f"{row[0]}:{row[1]}"


@st.cache_data(show_spinner=False)
def _cached_list_works(revision: str) -> list[dict[str, Any]]:
    return db.list_works()


@st.cache_data(show_spinner=False)
def _cached_seasonal_anime(year: int, season_code: str, include_unconfirmed: bool, revision: str) -> list[dict[str, Any]]:
    return db.list_seasonal_anime(year, season_code, include_unconfirmed=include_unconfirmed)


def works_snapshot() -> list[dict[str, Any]]:
    return [dict(work) for work in _cached_list_works(_live_data_revision())]


def seasonal_snapshot(year: int, season_code: str, include_unconfirmed: bool = True) -> list[dict[str, Any]]:
    return [
        dict(item)
        for item in _cached_seasonal_anime(
            int(year), str(season_code), bool(include_unconfirmed), _seasonal_data_revision(year, season_code)
        )
    ]


if READ_ONLY_MODE:
    @st.fragment(run_every="10s")
    def _watch_shared_database() -> None:
        revision = _live_data_revision()
        previous = st.session_state.get("_shared_data_revision")
        st.session_state._shared_data_revision = revision
        if previous is not None and previous != revision:
            st.rerun()

    _watch_shared_database()


@st.dialog("只读分享")
def _readonly_notice() -> None:
    st.warning("你没有该权限")
    st.caption("您没有操作权限；这是实时同步的只读页面。你可以查看、搜索和打开档案，但不能新增、编辑、删除或刷新主人的数据。")


def _block_readonly_action() -> bool:
    if not READ_ONLY_MODE:
        return False
    st.session_state.readonly_notice_pending = True
    return True


def _action_suffix() -> str:
    return f"&access={SHARE_TOKEN}" if READ_ONLY_MODE and SHARE_TOKEN else ""


def _handle_season_query_action() -> None:
    action = st.query_params.get("season_action")
    cache_id = st.query_params.get("season_id")
    if not action or not cache_id:
        return
    st.query_params.clear()
    if READ_ONLY_MODE and SHARE_TOKEN:
        st.query_params["access"] = SHARE_TOKEN
    if READ_ONLY_MODE and action != "open":
        st.session_state.readonly_notice_pending = True
        return
    try:
        if action == "open":
            if READ_ONLY_MODE:
                candidate = db.get_seasonal_anime(int(cache_id))
                work_id = int((candidate or {}).get("local_work_id") or 0)
                if not work_id:
                    st.session_state.readonly_notice_pending = True
                    return
            else:
                work_id = seasonal.open_candidate_for_scoring(int(cache_id))
            st.session_state.detail_return_page = "首页"
            st.session_state.detail_id = work_id
            st.session_state.edit_id = work_id
            st.session_state.nav_page = "条目详情"
        elif action in {"seen", "watching", "abandon"}:
            status = {"seen": "已看", "watching": "在看", "abandon": "弃置"}[action]
            work_id, _ = seasonal.set_candidate_status(int(cache_id), status)
            if action in {"seen", "abandon"}:
                st.session_state.detail_return_page = "首页"
                st.session_state.detail_id = work_id
                st.session_state.edit_id = work_id
                st.session_state.nav_page = "条目详情"
        st.rerun()
    except Exception as exc:
        st.session_state.season_action_error = str(exc)


_handle_season_query_action()
if st.session_state.pop("readonly_notice_pending", False):
    _readonly_notice()


def header(page_key: str, title: str, subtitle: str = "") -> None:
    render_page_shell(page_key, title, subtitle, APP_SETTINGS, works_snapshot())


def calc_diff(work: dict[str, Any]) -> float | None:
    return flt.calculate_score_diff(work)


def hydrated_works() -> list[dict[str, Any]]:
    works = works_snapshot()
    for work in works:
        work["score_diff"] = calc_diff(work)
    return works


def sort_works(works: list[dict[str, Any]], metric: str, limit: int) -> list[dict[str, Any]]:
    field = _rank_metric_map().get(metric, metric)
    if field == "special_public_high":
        candidates = [
            w for w in works
            if w.get("score_diff") is not None and float(w["score_diff"]) <= -1.0
        ]
        return sorted(candidates, key=lambda w: abs(float(w["score_diff"])), reverse=True)[:limit]
    if field == "special_mine_high":
        candidates = [
            w for w in works
            if w.get("score_diff") is not None and float(w["score_diff"]) >= 1.0
        ]
        return sorted(candidates, key=lambda w: abs(float(w["score_diff"])), reverse=True)[:limit]
    reverse = field != "score_diff_asc"
    if field == "score_diff_asc":
        field = "score_diff"
    valid = [w for w in works if _work_score_value(w, field) not in (None, "")]
    return sorted(valid, key=lambda w: _work_score_value(w, field), reverse=reverse)[:limit]


def sort_library(works: list[dict[str, Any]], label: str) -> list[dict[str, Any]]:
    field, reverse = LIBRARY_SORTS[label]
    return flt.sort_null_last(works, field, reverse)


def _pagination_bounds(total_items: int, page_size: int, page: int) -> tuple[int, int, int]:
    total_pages = max(1, (max(total_items, 0) + max(page_size, 1) - 1) // max(page_size, 1))
    page = min(max(int(page), 1), total_pages)
    start = (page - 1) * page_size
    return page, start, min(start + page_size, max(total_items, 0))


def _jump_to_page_state(page_state_key: str, jump_key: str, total_pages: int) -> None:
    st.session_state[page_state_key] = max(1, min(int(st.session_state.get(jump_key, 1) or 1), total_pages))


def _jump_to_rank_state(page_state_key: str, jump_key: str, page_size: int, max_rank: int) -> None:
    rank = max(1, min(int(st.session_state.get(jump_key, 1) or 1), max_rank))
    st.session_state[page_state_key] = (rank - 1) // page_size + 1


def _limit_value(value: Any, total: int) -> int:
    if str(value) == "全部":
        return max(total, 1)
    return min(max(int(value), 1), max(total, 1))


def _local_file_data_url(path: Path) -> str:
    if not path.exists() or not path.is_file():
        return ""
    mime = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
    return f"data:{mime};base64,{base64.b64encode(path.read_bytes()).decode('ascii')}"


def _static_url(path: Path) -> str:
    try:
        relative = path.resolve().relative_to((ROOT / "static").resolve()).as_posix()
    except ValueError:
        return _local_file_data_url(path)
    return f"/app/static/{quote(relative)}"


def _image_ext_from_response(url: str, content_type: str = "") -> str:
    suffix = Path(str(url).split("?", 1)[0]).suffix.lower()
    if suffix in {".jpg", ".jpeg", ".png", ".webp", ".gif"}:
        return ".jpg" if suffix == ".jpeg" else suffix
    lowered = content_type.lower()
    if "png" in lowered:
        return ".png"
    if "webp" in lowered:
        return ".webp"
    if "gif" in lowered:
        return ".gif"
    return ".jpg"


def _existing_bangumi_rank_cover(subject_id: int) -> Path | None:
    for path in BANGUMI_RANK_POSTER_DIR.glob(f"{int(subject_id)}.*"):
        if path.suffix.lower() in {".jpg", ".jpeg", ".png", ".webp", ".gif"} and path.is_file() and path.stat().st_size > 0:
            return path
    return None


def _cached_bangumi_rank_cover_url(subject_id: int, image_url: str | None) -> str:
    BANGUMI_RANK_POSTER_DIR.mkdir(parents=True, exist_ok=True)
    cached = _existing_bangumi_rank_cover(subject_id)
    if cached:
        return _static_url(cached)
    fallback = _local_file_data_url(ROOT / "covers" / "default.svg")
    if _running_under_streamlit_apptest():
        return fallback
    if not image_url:
        return fallback
    for _ in range(2):
        try:
            request = Request(str(image_url), headers=BANGUMI_IMAGE_HEADERS)
            with urlopen(request, timeout=10) as response:
                data = response.read()
                content_type = response.headers.get("content-type") or ""
            if not data or ("image" not in content_type.lower() and len(data) < 16):
                continue
            suffix = _image_ext_from_response(str(image_url), content_type)
            target = BANGUMI_RANK_POSTER_DIR / f"{int(subject_id)}{suffix}"
            temp = target.with_suffix(target.suffix + ".tmp")
            temp.write_bytes(data)
            temp.replace(target)
            return _static_url(target)
        except Exception:
            continue
    return fallback


def _bangumi_rank_cover_display_url(subject_id: int, image_url: str | None) -> str:
    cached = _existing_bangumi_rank_cover(subject_id)
    if cached:
        return _static_url(cached)
    if image_url:
        return str(image_url)
    return _local_file_data_url(ROOT / "covers" / "default.svg")


def _precache_bangumi_rank_covers(rows: list[dict[str, Any]]) -> None:
    if _running_under_streamlit_apptest():
        return
    BANGUMI_RANK_POSTER_DIR.mkdir(parents=True, exist_ok=True)
    pending = [
        (int(item["id"]), str(item.get("image") or ""))
        for item in rows
        if item.get("id") and item.get("image")
        and _existing_bangumi_rank_cover(int(item["id"])) is None
    ]
    if not pending:
        return
    def cache_pending() -> None:
        with ThreadPoolExecutor(max_workers=min(8, len(pending))) as executor:
            list(executor.map(lambda args: _cached_bangumi_rank_cover_url(*args), pending))
    threading.Thread(target=cache_pending, name="yanggumi-bangumi-covers", daemon=True).start()


def _bangumi_cover_html(src: str, title: str) -> str:
    safe_src = html.escape(src, quote=True)
    safe_title = html.escape(title or "Bangumi 封面", quote=True)
    return f'<div class="yg-bangumi-cover"><img src="{safe_src}" alt="{safe_title}" loading="lazy"></div>'


def render_jump_pager(
    *,
    key_prefix: str,
    total_items: int,
    page_size: int,
    current_page: int,
    max_jump_rank: int | None = None,
    top: bool = True,
) -> tuple[int, int, int]:
    page, start, end = _pagination_bounds(total_items, page_size, current_page)
    total_pages = max(1, (max(total_items, 0) + page_size - 1) // page_size)
    rank_limit = max_jump_rank or max(total_items, 1)
    page_state_key = f"{key_prefix}_page"
    label = "top" if top else "bottom"
    c1, c2, c3, c4, c5 = st.columns([.9, 1.7, .9, 1.05, 1.05], vertical_alignment="center")
    if c1.button("上一页", disabled=page <= 1, use_container_width=True, key=f"{key_prefix}_prev_{label}_{page}_{page_size}"):
        st.session_state[page_state_key] = page - 1
        st.rerun()
    c2.markdown(
        f'<div class="yg-bangumi-rank-page">第 {page} / {total_pages} 页 · #{start + 1 if total_items else 0} - #{end} · 共 {total_items}</div>',
        unsafe_allow_html=True,
    )
    if c3.button("下一页", disabled=page >= total_pages, use_container_width=True, key=f"{key_prefix}_next_{label}_{page}_{page_size}"):
        st.session_state[page_state_key] = page + 1
        st.rerun()
    jump_page_key = f"{key_prefix}_jump_page_{label}_{page}_{page_size}"
    jump_rank_key = f"{key_prefix}_jump_rank_{label}_{page}_{page_size}"
    jump_page = c4.number_input(
        "跳到页", min_value=1, max_value=total_pages, value=page, step=1,
        key=jump_page_key, on_change=_jump_to_page_state, args=(page_state_key, jump_page_key, total_pages),
    )
    jump_rank = c5.number_input(
        "跳到名次", min_value=1, max_value=max(rank_limit, 1),
        value=min(max(start + 1, 1), max(rank_limit, 1)), step=1,
        key=jump_rank_key, on_change=_jump_to_rank_state, args=(page_state_key, jump_rank_key, page_size, max(rank_limit, 1)),
    )
    j1, j2 = st.columns([1, 1])
    if j1.button("跳页", use_container_width=True, key=f"{key_prefix}_jump_page_button_{label}_{page}_{page_size}"):
        st.session_state[page_state_key] = max(1, min(int(st.session_state.get(jump_page_key, jump_page) or 1), total_pages))
        st.rerun()
    if j2.button("跳名次", use_container_width=True, key=f"{key_prefix}_jump_rank_button_{label}_{page}_{page_size}"):
        rank = max(1, min(int(st.session_state.get(jump_rank_key, jump_rank) or 1), max(rank_limit, 1)))
        st.session_state[page_state_key] = (rank - 1) // page_size + 1
        st.rerun()
    return page, start, end


def score_interval(value: Any, selected: str) -> bool:
    return flt.score_in_range(value, selected)


def diff_interval(value: Any, selected: str) -> bool:
    return flt.diff_abs_in_range(value, selected)


def _season_status_label(value: str | None, score: Any = None) -> str:
    if score is not None or value == "已看":
        return "已看"
    return "抛弃" if value == "弃置" else (value or "未看")


def _release_quarter(value: Any) -> int | None:
    try:
        return (date.fromisoformat(str(value)[:10]).month - 1) // 3 + 1
    except (TypeError, ValueError):
        return None


def _set_season_candidate_status(candidate: dict[str, Any], status: str) -> None:
    if _block_readonly_action():
        return
    try:
        work_id, should_edit = seasonal.set_candidate_status(int(candidate["id"]), status)
        if should_edit:
            st.session_state.detail_return_page = "首页"
            st.session_state.detail_id = work_id
            st.session_state.edit_id = work_id
            st.session_state.nav_page = "条目详情"
        st.rerun()
    except Exception as exc:
        st.error(f"状态保存失败：{exc}")


def render_seasonal_anime_panel(works: list[dict[str, Any]]) -> None:
    season = seasonal.current_season()
    daily_sync_key = f"season_daily_sync_{season['year']}_{season['season_code']}_{date.today().isoformat()}"
    if not READ_ONLY_MODE and not _running_under_streamlit_apptest() and not st.session_state.get(daily_sync_key):
        try:
            seasonal.refresh_current_season_if_due()
        except Exception as exc:
            st.session_state.season_action_error = str(exc)
        st.session_state[daily_sync_key] = True
    reclass_key = f"season_reclassified_{season['year']}_{season['season_code']}"
    if not READ_ONLY_MODE and not st.session_state.get(reclass_key):
        seasonal.reclassify_cached_season(season["year"], season["season_code"])
        st.session_state[reclass_key] = True
    meta = db.seasonal_cache_meta(season["year"], season["season_code"])

    title_col, refresh_col = st.columns([5, 1], vertical_alignment="bottom")
    with title_col:
        render_section_heading("本季新番", "CURRENT SEASON", f'{season["year"]} · {season["month_label"]}')
        st.caption("根据本地现实时间自动切换 · 官方 Bangumi 公开条目 · 主区仅显示日本动画")
    if refresh_col.button("刷新本季新番", use_container_width=True, key="refresh_seasonal_anime"):
        if _block_readonly_action():
            _readonly_notice()
            return
        with st.spinner("正在刷新本季新番…"):
            try:
                _, count = seasonal.refresh_current_season()
                st.success(f"已同步 {count} 个候选条目。")
            except Exception as exc:
                st.error(f"刷新失败：{exc}")
        st.rerun()

    season_rows = seasonal_snapshot(season["year"], season["season_code"], include_unconfirmed=True)
    items = [item for item in season_rows if seasonal.is_homepage_seasonal_anime(item)]
    if not items:
        error = (meta or {}).get("error")
        render_empty_state("本季候选池暂时为空", error or "点击“刷新本季新番”即可从 Bangumi 获取公开条目数据。", "季")
        return

    with st.container(key="seasonal_filters"):
        c1, c2, c3 = st.columns([1.5, 1.2, 2.3])
        status_filter = c1.segmented_control(
            "状态", ["全部", "未看", "想看", "在看", "抛弃", "已看"], default="全部", key="season_status_filter"
        ) or "全部"
        sort_label = c2.selectbox("排序", ["放送时间", "Bangumi 评分", "评分人数", "我的状态", "更新时间"], key="season_sort")
        query = c3.text_input("搜索本季新番", placeholder="中文名、原名或标签", key="season_query").strip().casefold()
    if query:
        items = [item for item in items if query in " ".join([
            str(item.get("title") or ""), str(item.get("original_title") or ""), str(item.get("tags_json") or "")
        ]).casefold()]
    if status_filter != "全部":
        items = [item for item in items if _season_status_label(item.get("effective_status"), item.get("local_score")) == status_filter]
    if status_filter in {"未看", "想看", "在看", "抛弃"}:
        items = [item for item in items if seasonal.is_tv_seasonal_anime(item)]
    def broadcast_order(item: dict[str, Any]) -> tuple[Any, ...]:
        subject = seasonal.candidate_subject(item)
        value = subject.get("_yanggumi_broadcast_sort")
        if str(value).isdigit():
            return (0, int(value), int(item.get("bangumi_id") or 0))
        return (1, item.get("air_date") or item.get("release_date") or "9999-99-99", int(item.get("bangumi_id") or 0))

    sorters = {
        "放送时间": broadcast_order,
        "Bangumi 评分": lambda x: -(float(x.get("bangumi_score") or -1)),
        "评分人数": lambda x: -(int(x.get("bangumi_total_votes") or -1)),
        "我的状态": lambda x: {"在看": 0, "想看": 1, None: 2, "弃置": 3, "已看": 4}.get(x.get("effective_status"), 5),
        "更新时间": lambda x: str(x.get("updated_at") or ""),
    }
    items.sort(key=sorters[sort_label], reverse=sort_label == "更新时间")
    if not items:
        render_empty_state("当前筛选没有新番", "调整状态、关键词或排序即可恢复候选条目。", "⌕")
        return

    carousel_items = []
    for item in items:
        status = _season_status_label(item.get("effective_status"), item.get("local_score"))
        local_poster = seasonal.seasonal_poster_static_url(season["year"], season["season_code"], int(item["bangumi_id"]))
        subject = seasonal.candidate_subject(item)
        broadcast_day = subject.get("_yanggumi_broadcast_day")
        broadcast_time = str(subject.get("_yanggumi_broadcast_time") or "")
        broadcast_day_label = str(subject.get("_yanggumi_broadcast_day_label") or "")
        broadcast_note = str(subject.get("_yanggumi_broadcast_note") or "")
        broadcast_label = ""
        if str(broadcast_day).isdigit() and broadcast_time:
            broadcast_label = f"{broadcast_day_label} {broadcast_time} 电视台播出"
            if broadcast_note:
                broadcast_label += f" · {broadcast_note}"
        carousel_items.append({
            "id": int(item["id"]), "title": item.get("title") or "未命名动画",
            "original": item.get("original_title") or "", "image": local_poster or item.get("image_url") or "",
            "remote_image": item.get("image_url") or "",
            "score": fmt_score(item.get("bangumi_score")), "votes": int(item.get("bangumi_total_votes") or 0),
            "date": item.get("air_date") or "日期未定", "status": status,
            "broadcast_day": int(broadcast_day) if str(broadcast_day).isdigit() else None,
            "broadcast_sort": int(subject.get("_yanggumi_broadcast_sort") or 999999),
            "broadcast_label": broadcast_label,
        })
    action_suffix = _action_suffix()
    slider_key = f"season_carousel_index_{season['year']}_{season['season_code']}_{status_filter}_{sort_label}_{query}"
    if slider_key not in st.session_state:
        st.session_state[slider_key] = random.SystemRandom().randrange(len(carousel_items))
    st.session_state[slider_key] %= len(carousel_items)
    motion_key = f"{slider_key}_motion"
    if motion_key not in st.session_state:
        st.session_state[motion_key] = "idle"
    for item in carousel_items:
        status = item["status"]
        item["state"] = "已看" if status == "已看" else "已追番" if status == "在看" else "已抛弃" if status == "抛弃" else ""
        item["open_url"] = f"/?season_action=open&season_id={item['id']}{action_suffix}"
        item["seen_url"] = f"/?season_action=seen&season_id={item['id']}{action_suffix}"
        item["watching_url"] = f"/?season_action=watching&season_id={item['id']}{action_suffix}"
        item["abandon_url"] = f"/?season_action=abandon&season_id={item['id']}{action_suffix}"

    current = int(st.session_state[slider_key])
    page_label = f"第 {current + 1} / {len(carousel_items)} 部"
    pos_map = {-2: "pos_m2", -1: "pos_m1", 0: "pos_c", 1: "pos_p1", 2: "pos_p2"}
    desired_offsets = [-2, -1, 0, 1, 2] if len(carousel_items) >= 5 else [-1, 0, 1] if len(carousel_items) >= 3 else [0, 1] if len(carousel_items) == 2 else [0]
    display_slots: list[tuple[int, int]] = []
    seen_indexes: set[int] = set()
    seen_signatures: set[str] = set()

    def item_signature(item: dict[str, Any]) -> str:
        return str(item.get("image") or item.get("title") or item.get("id") or "")

    def add_slot(position: int, preferred: int, step: int) -> None:
        for distance in range(len(carousel_items)):
            item_index = (preferred + distance * step) % len(carousel_items)
            item = carousel_items[item_index]
            signature = item_signature(item)
            if item_index in seen_indexes or signature in seen_signatures:
                continue
            seen_indexes.add(item_index)
            seen_signatures.add(signature)
            display_slots.append((position, item_index))
            return

    add_slot(0, current, 1)
    for position in [offset for offset in desired_offsets if offset < 0]:
        add_slot(position, current + position, -1)
    for position in [offset for offset in desired_offsets if offset > 0]:
        add_slot(position, current + position, 1)
    display_slots.sort(key=lambda slot: slot[0])

    carousel_payload = json.dumps(carousel_items, ensure_ascii=False).replace("</", "<\\/")
    component_id = f"yg-season-live-{secrets.token_hex(4)}"
    component_html = f"""
    <div id="{component_id}" class="yg-season-live" data-current="{current}">
      <script type="application/json" id="{component_id}-data">{carousel_payload}</script>
      <iframe name="yg-season-action-frame" class="yg-season-action-frame" title=""></iframe>
      <nav class="yg-season-week" aria-label="按放送日跳转">
        <button type="button" data-broadcast-day="0">周一</button><button type="button" data-broadcast-day="1">周二</button>
        <button type="button" data-broadcast-day="2">周三</button><button type="button" data-broadcast-day="3">周四</button>
        <button type="button" data-broadcast-day="4">周五</button><button type="button" data-broadcast-day="5">周六</button>
        <button type="button" data-broadcast-day="6">周日</button>
      </nav>
      <div class="yg-season-live-page"></div>
      <div class="yg-season-live-shell">
        <button class="yg-season-arrow prev" type="button" aria-label="上一部">‹</button>
        <section class="yg-season-live-stage"></section>
        <button class="yg-season-arrow next" type="button" aria-label="下一部">›</button>
      </div>
      <div class="yg-season-progress is-running" aria-hidden="true"><span></span></div>
    </div>
    <style>
      html,body{{margin:0;background:transparent;color:#e7e7e9;font-family:Inter,"Segoe UI","Microsoft YaHei",sans-serif;overflow:hidden;}}
      .yg-season-live{{position:relative;width:100%;height:683px;box-sizing:border-box;}}
      .yg-season-action-frame{{position:absolute;width:0;height:0;border:0;opacity:0;pointer-events:none;}}
      .yg-season-week{{display:grid;grid-template-columns:repeat(7,minmax(0,1fr));gap:7px;height:40px;margin:0 auto 7px;width:min(900px,88%);}}
      .yg-season-week button{{min-width:0;border:1px solid #34353a;border-radius:8px;background:#202126;color:#aaa;font-size:14px;font-weight:800;cursor:pointer;}}
      .yg-season-week button:hover,.yg-season-week button.is-active{{border-color:#d65a7a;background:#37242c;color:#ff9ab7;}}
      .yg-season-week button:disabled{{cursor:default;opacity:.3;}}
      .yg-season-live-page{{height:30px;margin:0 0 8px;text-align:center;color:#e18aa1;font-size:17px;font-weight:900;line-height:30px;}}
      .yg-season-live-shell{{display:grid;grid-template-columns:92px minmax(0,1fr) 92px;align-items:center;gap:14px;height:585px;}}
      .yg-season-live-stage{{position:relative;height:565px;overflow:hidden;border-radius:18px;background:radial-gradient(circle at 50% 34%,rgba(214,90,122,.12),transparent 36%),#1f2023;}}
      .yg-season-arrow{{display:grid;place-items:center;width:82px;height:92px;margin:auto;padding:0;border:1px solid rgba(214,90,122,.38);border-radius:18px;background:#202126;color:#d8d8dc;font-size:34px;font-weight:900;line-height:1;cursor:pointer;box-shadow:0 14px 32px rgba(0,0,0,.22);}}
      .yg-season-arrow:hover{{border-color:rgba(214,90,122,.72);background:#28292d;color:#fff;}}
      .yg-season-card{{position:absolute;top:24px;left:50%;width:230px;min-width:0;opacity:0;filter:brightness(.86);transform:translate3d(-50%,0,0) scale(.58);will-change:transform,opacity,filter;backface-visibility:hidden;contain:layout paint style;}}
      .yg-season-card.pos_c{{z-index:5;opacity:1;filter:none;transform:translate3d(-50%,0,0) scale(1.08);}}
      .yg-season-card.pos_m1{{z-index:4;opacity:.82;filter:brightness(.9);transform:translate3d(-174%,0,0) scale(.88);}}
      .yg-season-card.pos_p1{{z-index:4;opacity:.82;filter:brightness(.9);transform:translate3d(74%,0,0) scale(.88);}}
      .yg-season-card.pos_m2{{z-index:3;opacity:.58;filter:brightness(.88);transform:translate3d(-292%,0,0) scale(.68);}}
      .yg-season-card.pos_p2{{z-index:3;opacity:.58;filter:brightness(.88);transform:translate3d(192%,0,0) scale(.68);}}
      .yg-season-card.pos_off{{opacity:0;pointer-events:none;transform:translate3d(-50%,0,0) scale(.58);}}
      .yg-season-card.anim-next-pos_m2{{animation:yg-live-next-m2 .62s cubic-bezier(.18,.78,.2,1) both;}}
      .yg-season-card.anim-next-pos_m1{{animation:yg-live-next-m1 .62s cubic-bezier(.18,.78,.2,1) both;}}
      .yg-season-card.anim-next-pos_c{{animation:yg-live-next-c .62s cubic-bezier(.18,.78,.2,1) both;}}
      .yg-season-card.anim-next-pos_p1{{animation:yg-live-next-p1 .62s cubic-bezier(.18,.78,.2,1) both;}}
      .yg-season-card.anim-next-pos_p2{{animation:yg-live-next-p2 .62s cubic-bezier(.18,.78,.2,1) both;}}
      .yg-season-card.anim-prev-pos_m2{{animation:yg-live-prev-m2 .62s cubic-bezier(.18,.78,.2,1) both;}}
      .yg-season-card.anim-prev-pos_m1{{animation:yg-live-prev-m1 .62s cubic-bezier(.18,.78,.2,1) both;}}
      .yg-season-card.anim-prev-pos_c{{animation:yg-live-prev-c .62s cubic-bezier(.18,.78,.2,1) both;}}
      .yg-season-card.anim-prev-pos_p1{{animation:yg-live-prev-p1 .62s cubic-bezier(.18,.78,.2,1) both;}}
      .yg-season-card.anim-prev-pos_p2{{animation:yg-live-prev-p2 .62s cubic-bezier(.18,.78,.2,1) both;}}
      @keyframes yg-live-next-m2{{from{{opacity:.82;filter:brightness(.9);transform:translate3d(-174%,0,0) scale(.88);}}to{{opacity:.58;filter:brightness(.88);transform:translate3d(-292%,0,0) scale(.68);}}}}
      @keyframes yg-live-next-m1{{from{{opacity:1;filter:none;transform:translate3d(-50%,0,0) scale(1.08);}}to{{opacity:.82;filter:brightness(.9);transform:translate3d(-174%,0,0) scale(.88);}}}}
      @keyframes yg-live-next-c{{from{{opacity:.82;filter:brightness(.9);transform:translate3d(74%,0,0) scale(.88);}}to{{opacity:1;filter:none;transform:translate3d(-50%,0,0) scale(1.08);}}}}
      @keyframes yg-live-next-p1{{from{{opacity:.58;filter:brightness(.88);transform:translate3d(192%,0,0) scale(.68);}}to{{opacity:.82;filter:brightness(.9);transform:translate3d(74%,0,0) scale(.88);}}}}
      @keyframes yg-live-next-p2{{from{{opacity:0;filter:brightness(.86);transform:translate3d(260%,0,0) scale(.58);}}to{{opacity:.58;filter:brightness(.88);transform:translate3d(192%,0,0) scale(.68);}}}}
      @keyframes yg-live-prev-m2{{from{{opacity:0;filter:brightness(.86);transform:translate3d(-360%,0,0) scale(.58);}}to{{opacity:.58;filter:brightness(.88);transform:translate3d(-292%,0,0) scale(.68);}}}}
      @keyframes yg-live-prev-m1{{from{{opacity:.58;filter:brightness(.88);transform:translate3d(-292%,0,0) scale(.68);}}to{{opacity:.82;filter:brightness(.9);transform:translate3d(-174%,0,0) scale(.88);}}}}
      @keyframes yg-live-prev-c{{from{{opacity:.82;filter:brightness(.9);transform:translate3d(-174%,0,0) scale(.88);}}to{{opacity:1;filter:none;transform:translate3d(-50%,0,0) scale(1.08);}}}}
      @keyframes yg-live-prev-p1{{from{{opacity:1;filter:none;transform:translate3d(-50%,0,0) scale(1.08);}}to{{opacity:.82;filter:brightness(.9);transform:translate3d(74%,0,0) scale(.88);}}}}
      @keyframes yg-live-prev-p2{{from{{opacity:.82;filter:brightness(.9);transform:translate3d(74%,0,0) scale(.88);}}to{{opacity:.58;filter:brightness(.88);transform:translate3d(192%,0,0) scale(.68);}}}}
      .yg-season-poster{{position:relative;display:block;height:330px;border:1px solid rgba(214,90,122,.25);border-radius:16px;overflow:hidden;background:linear-gradient(135deg,#1b1d24,#2a2028 48%,#17282f);box-shadow:0 18px 38px rgba(0,0,0,.34);}}
      .yg-season-poster::before{{content:"";position:absolute;inset:0;background:linear-gradient(110deg,rgba(255,255,255,.02),rgba(255,255,255,.07),rgba(255,255,255,.02));transform:translateX(-100%);animation:yg-season-poster-wait 1.4s ease-in-out infinite;}}
      .yg-season-poster.is-loaded::before,.yg-season-poster.is-missing::before{{display:none;}}
      .yg-season-poster img{{position:absolute;inset:0;display:block;width:100%;height:100%;object-fit:cover;border:0;}}
      @keyframes yg-season-poster-wait{{to{{transform:translateX(100%);}}}}
      .yg-season-poster.is-missing{{display:grid;place-items:center;padding:18px;text-align:center;}}
      .yg-season-poster.is-missing::after{{content:attr(data-fallback);color:#f1d5de;font-size:16px;font-weight:900;line-height:1.35;text-shadow:0 2px 12px rgba(0,0,0,.38);}}
      .yg-season-card h3{{margin:13px 0 4px;font-size:19px;line-height:1.32;color:#eee;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;}}
      .yg-season-original{{height:19px;color:#8f9199;font-size:12px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;}}
      .yg-season-broadcast{{height:18px;margin-top:5px;color:#e18aa1;font-size:12px;font-weight:800;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;}}
      .yg-season-meta{{margin-top:6px;color:#aaa;font-size:12px;}}
      .yg-season-actions{{display:flex;gap:7px;margin-top:8px;}}
      .yg-season-actions a{{display:grid;place-items:center;flex:1;height:33px;border:1px solid #34353a;border-radius:9px;background:#202126;color:#aaa;text-decoration:none;font-size:17px;}}
      .yg-season-actions .seen-active{{background:#a52d4e;color:white;border-color:#ff7699;}}
      .yg-season-actions .watching-active{{background:#267b55;color:white;border-color:#55c98c;}}
      .yg-season-actions .abandon-active{{background:#9a3030;color:white;border-color:#ef6b6b;}}
      .yg-season-state{{height:18px;margin-top:6px;font-size:12px;font-weight:800;text-align:center;color:#ff8baa;}}
      .yg-season-progress{{position:absolute;bottom:5px;left:50%;z-index:8;width:min(760px,58%);height:6px;overflow:hidden;border:1px solid rgba(214,90,122,.3);border-radius:999px;background:linear-gradient(180deg,rgba(12,12,15,.78),rgba(36,28,34,.78));box-shadow:inset 0 1px 8px rgba(0,0,0,.46),0 0 18px rgba(214,90,122,.12);transform:translateX(-50%);pointer-events:none;}}
      .yg-season-progress span{{display:block;width:100%;height:100%;border-radius:999px;background:linear-gradient(90deg,#aa2d55,#e65c88 58%,#ff9ab7);box-shadow:0 0 16px rgba(232,92,136,.58);transform:scaleX(0);transform-origin:left center;}}
      .yg-season-progress.is-running span{{animation:yg-season-progress-fill 10s linear infinite;}}
      @keyframes yg-season-progress-fill{{from{{transform:scaleX(0);}}to{{transform:scaleX(1);}}}}
      @media (max-width:760px){{.yg-season-week{{width:96%;gap:3px;}}.yg-season-week button{{font-size:12px;}}.yg-season-live-shell{{grid-template-columns:70px minmax(0,1fr) 70px;gap:8px;}}.yg-season-arrow{{width:62px;height:78px;}}.yg-season-card.pos_m2,.yg-season-card.pos_p2{{opacity:0;visibility:hidden;}}}}
    </style>
    <script>
      (() => {{
        const root = document.getElementById('{component_id}');
        const stage = root.querySelector('.yg-season-live-stage');
        const page = root.querySelector('.yg-season-live-page');
        const weekButtons = Array.from(root.querySelectorAll('[data-broadcast-day]'));
        const items = JSON.parse(document.getElementById('{component_id}-data').textContent || '[]');
        const storageKey = {json.dumps(f"yanggumi:{slider_key}", ensure_ascii=False)};
        let current = Number(root.dataset.current || 0) % Math.max(items.length, 1);
        try {{
          const saved = Number(window.parent.sessionStorage.getItem(storageKey));
          if (Number.isInteger(saved) && saved >= 0 && saved < items.length) current = saved;
        }} catch (error) {{}}
        let autoTimer = null;
        let moving = false;
        const autoDelay = 10000;
        const offsets = items.length >= 5 ? [-2,-1,0,1,2] : items.length >= 3 ? [-1,0,1] : items.length === 2 ? [0,1] : [0];
        const pos = {{'-2':'pos_m2','-1':'pos_m1','0':'pos_c','1':'pos_p1','2':'pos_p2'}};
        const cards = offsets.map(() => {{
          const card = document.createElement('article');
          card.className = 'yg-season-card';
          stage.appendChild(card);
          return card;
        }});
        const esc = (value) => String(value ?? '').replace(/[&<>"']/g, ch => ({{'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}}[ch]));
        const attr = (value) => esc(value).replace(/`/g, '&#96;');
        const posterCache = new Map();
        function preloadImage(url) {{
          if (!url) return null;
          if (posterCache.has(url)) return posterCache.get(url);
          const record = {{ img: new Image(), loaded: false, failed: false, callbacks: [] }};
          record.img.decoding = 'async';
          record.img.loading = 'eager';
          record.img.referrerPolicy = 'no-referrer';
          record.img.onload = () => {{
            record.loaded = true;
            record.callbacks.splice(0).forEach(callback => callback(record));
          }};
          record.img.onerror = () => {{
            record.failed = true;
            record.callbacks.splice(0).forEach(callback => callback(record));
          }};
          posterCache.set(url, record);
          record.img.src = url;
          return record;
        }}
        function warmPosterCache() {{
          items.forEach((item, index) => {{
            const url = item.image || item.remote_image || '';
            if (!url) return;
            const link = document.createElement('link');
            link.rel = 'preload';
            link.as = 'image';
            link.href = url;
            document.head.appendChild(link);
            window.setTimeout(() => preloadImage(url), index * 25);
          }});
        }}
        function signature(item) {{
          return String(item?.image || item?.title || item?.id || '');
        }}
        function addSlot(slots, seenIndexes, seenSignatures, position, preferred, step) {{
          for (let distance = 0; distance < items.length; distance += 1) {{
            const index = (preferred + distance * step + items.length) % items.length;
            const item = items[index];
            const sig = signature(item);
            if (seenIndexes.has(index) || seenSignatures.has(sig)) continue;
            seenIndexes.add(index);
            seenSignatures.add(sig);
            slots.push([position, index]);
            return;
          }}
        }}
        function displaySlots() {{
          const slots = [];
          const seenIndexes = new Set();
          const seenSignatures = new Set();
          addSlot(slots, seenIndexes, seenSignatures, 0, current, 1);
          offsets.filter(offset => offset < 0).forEach(offset => addSlot(slots, seenIndexes, seenSignatures, offset, current + offset, -1));
          offsets.filter(offset => offset > 0).forEach(offset => addSlot(slots, seenIndexes, seenSignatures, offset, current + offset, 1));
          return slots.sort((a, b) => a[0] - b[0]);
        }}
        function cardBody(item) {{
          const status = item.status || '';
          const state = item.state || '';
          const broadcast = item.broadcast_label ? `<div class="yg-season-broadcast">${{esc(item.broadcast_label)}}</div>` : '<div class="yg-season-broadcast">&nbsp;</div>';
          return `<a class="yg-season-poster" data-season-go="1" data-image-token="${{attr(item.id)}}-${{attr(item.image || '')}}" data-fallback="${{attr(item.title)}}" href="${{attr(item.open_url)}}" target="_top" title="打开档案"></a>
            <h3>${{esc(item.title)}}</h3><div class="yg-season-original">${{esc(item.original || '')}}</div>
            ${{broadcast}}<div class="yg-season-meta">BGM ${{esc(item.score)}} · ${{Number(item.votes || 0).toLocaleString()}} 人 · ${{esc(status)}}</div>
            <div class="yg-season-actions">
              <a data-season-go="1" class="${{status === '已看' ? 'seen-active' : ''}}" href="${{attr(item.seen_url)}}" target="_top" title="已看并评分">♡</a>
              <a data-season-watch="1" class="${{status === '在看' ? 'watching-active' : ''}}" href="${{attr(item.watching_url)}}" target="yg-season-action-frame" title="已追番">${{status === '在看' ? '已追番' : '○'}}</a>
              <a data-season-go="1" class="${{status === '抛弃' ? 'abandon-active' : ''}}" href="${{attr(item.abandon_url)}}" target="_top" title="抛弃并评分">×</a>
            </div><div class="yg-season-state">${{esc(state)}}</div>`;
        }}
        function loadPoster(card, item) {{
          const poster = card.querySelector('.yg-season-poster');
          if (!poster) return;
          const token = poster.dataset.imageToken || '';
          const imageUrl = item.image || '';
          if (!imageUrl) {{
            poster.classList.add('is-missing');
            return;
          }}
          const record = preloadImage(imageUrl);
          const showImage = () => {{
            if (!poster.isConnected || poster.dataset.imageToken !== token) return;
            const img = record.img.cloneNode(false);
            img.alt = item.title || '';
            poster.textContent = '';
            poster.appendChild(img);
            poster.classList.add('is-loaded');
          }};
          if (record?.loaded) {{
            showImage();
          }} else if (record?.failed) {{
            poster.classList.add('is-missing');
          }} else if (record) {{
            record.callbacks.push(() => {{
              if (record.loaded) showImage();
              if (record.failed && poster.isConnected && poster.dataset.imageToken === token) poster.classList.add('is-missing');
            }});
          }}
        }}
        function animationClass(finalClass, delta) {{
          if (!delta || finalClass === 'pos_off') return '';
          return `anim-${{delta === 1 ? 'next' : 'prev'}}-${{finalClass}}`;
        }}
        function render(instant=false, delta=0) {{
          try {{ window.parent.sessionStorage.setItem(storageKey, String(current)); }} catch (error) {{}}
          page.textContent = `第 ${{current + 1}} / ${{items.length}} 部`;
          const rawActiveDay = items[current]?.broadcast_day;
          const activeDay = rawActiveDay === null || rawActiveDay === undefined ? -1 : Number(rawActiveDay);
          weekButtons.forEach(button => button.classList.toggle('is-active', Number(button.dataset.broadcastDay) === activeDay));
          const slots = displaySlots();
          cards.forEach((card, index) => {{
            const slot = slots[index];
            if (!slot) {{
              card.innerHTML = '';
              card.dataset.itemId = '';
              card.dataset.itemTitle = '';
              card.dataset.expectedImage = '';
              card.className = 'yg-season-card pos_off';
              return;
            }}
            const [offset, itemIndex] = slot;
            const finalClass = pos[String(offset)] || 'pos_off';
            const item = items[itemIndex];
            card.dataset.itemId = String(item.id || '');
            card.dataset.itemTitle = item.title || '';
            card.dataset.expectedImage = item.image || '';
            card.innerHTML = cardBody(item);
            loadPoster(card, item);
            card.style.animation = 'none';
            card.className = `yg-season-card ${{finalClass}}`;
            card.offsetHeight;
            card.style.animation = '';
            const motion = instant ? '' : animationClass(finalClass, delta);
            if (motion) {{
              card.classList.add(motion);
              card.addEventListener('animationend', () => card.classList.remove(motion), {{ once: true }});
            }}
          }});
        }}
        function restartAuto() {{
          if (autoTimer) window.clearTimeout(autoTimer);
          const progress = root.querySelector('.yg-season-progress');
          if (!progress) return;
          progress.classList.remove('is-running');
          progress.offsetHeight;
          if (items.length > 1) {{
            progress.classList.add('is-running');
            autoTimer = window.setTimeout(() => move(1, false), autoDelay);
          }}
        }}
        function move(delta, manual=true) {{
          if (items.length < 2 || moving) return;
          moving = true;
          current = (current + delta + items.length) % items.length;
          render(false, delta);
          restartAuto();
          window.setTimeout(() => {{ moving = false; }}, 680);
        }}
        function navigateTop(href) {{
          const target = new URL(href, window.parent.location.origin).toString();
          try {{
            window.parent.history.pushState(null, '', target);
            window.parent.location.reload();
          }} catch (error) {{
            try {{
              window.parent.location.href = target;
            }} catch (fallbackError) {{
              window.open(target, '_top');
            }}
          }}
        }}
        root.addEventListener('click', (event) => {{
          const watchLink = event.target.closest('a[data-season-watch="1"]');
          if (watchLink) {{
            watchLink.classList.add('watching-active');
            watchLink.textContent = '已追番';
            const state = watchLink.closest('.yg-season-card')?.querySelector('.yg-season-state');
            if (state) state.textContent = '已追番';
            const href = watchLink.getAttribute('href') || '';
            const match = href.match(/season_id=(\\d+)/);
            if (match) {{
              const item = items.find(value => String(value.id) === match[1]);
              if (item) {{
                item.status = '在看';
                item.state = '已追番';
              }}
            }}
            return;
          }}
          const link = event.target.closest('a[data-season-go="1"]');
          if (!link) return;
          event.preventDefault();
          navigateTop(link.getAttribute('href') || link.href);
        }});
        root.querySelector('.prev').addEventListener('click', () => move(-1));
        root.querySelector('.next').addEventListener('click', () => move(1));
        weekButtons.forEach(button => {{
          const day = Number(button.dataset.broadcastDay);
          const matches = items.map((item, index) => [item, index]).filter(([item]) =>
            item.broadcast_day !== null && item.broadcast_day !== undefined && Number(item.broadcast_day) === day
          );
          button.disabled = matches.length === 0;
          button.addEventListener('click', () => {{
            if (!matches.length) return;
            matches.sort((a, b) => Number(a[0].broadcast_sort || 999999) - Number(b[0].broadcast_sort || 999999));
            current = matches[0][1];
            moving = false;
            render(true, 0);
            restartAuto();
          }});
        }});
        warmPosterCache();
        render(true, 0);
        restartAuto();
      }})();
    </script>
    """
    components.html(component_html, height=700, scrolling=False)

    all_items = [item for item in season_rows if seasonal.is_homepage_seasonal_anime(item)]
    local_scored = [item for item in all_items if item.get("local_score") is not None]
    history = []
    for years_ago in (5, 10, 20):
        target_year = season["year"] - years_ago
        values = [float(work["score_total"]) for work in works if work.get("type") == "动画" and work.get("score_total") is not None
                  and flt.derive_year(work) == target_year and _release_quarter(work.get("release_date")) == int(season["season_code"][1:])]
        if values:
            history.append(f"{target_year} · {sum(values)/len(values):.2f}")
    metrics = st.columns(7)
    metric_values = [
        ("本季新番", len(all_items)), ("在看", sum(_season_status_label(x.get("effective_status"), x.get("local_score")) == "在看" for x in all_items)),
        ("想看", sum(_season_status_label(x.get("effective_status"), x.get("local_score")) == "想看" for x in all_items)),
        ("抛弃", sum(_season_status_label(x.get("effective_status"), x.get("local_score")) == "抛弃" for x in all_items)),
        ("已看 / 已评分", len(local_scored)), ("本季均分", fmt_score(flt.average_non_null(local_scored, "local_score"))),
        ("历史同季", " · ".join(history) if history else "暂无"),
    ]
    for col, (label, value) in zip(metrics, metric_values):
        col.metric(label, value)

    unconfirmed = [item for item in season_rows if item.get("source_status") == "unconfirmed"]
    if unconfirmed:
        with st.expander(f"未确认来源（{len(unconfirmed)}）", expanded=False):
            st.caption("这些条目是 Bangumi 动画，但公开字段不足以确认日本来源，因此不混入主候选区。")
            for item in unconfirmed[:30]:
                st.write(f"{item.get('title')} · {item.get('original_title') or ''} · {item.get('air_date') or '日期未定'}")


def render_daily_art() -> None:
    manifest = daily_art.load_manifest()
    refreshing_art = daily_art.refresh_manifest_async_if_needed(manifest)
    portraits = daily_art.browser_candidates(manifest["items"], "portrait")
    wallpapers = daily_art.browser_candidates(manifest["items"], "wallpaper")

    def choose_art(force_wallpaper: bool | None = None) -> None:
        use_wallpaper = bool(wallpapers) and (
            force_wallpaper is True or (force_wallpaper is None and random.SystemRandom().random() < 0.10)
        )
        if force_wallpaper is False:
            use_wallpaper = False
        if use_wallpaper and not wallpapers:
            use_wallpaper = False
        if not portraits and wallpapers:
            use_wallpaper = True
        kind = "wallpaper" if use_wallpaper else "portrait"
        source = wallpapers if use_wallpaper else portraits
        pick_limit = 1 if use_wallpaper else 3
        recent_key = f"daily_art_recent_{kind}"
        recent = list(st.session_state.get(recent_key, []))
        pool = [item for item in source if item["key"] not in recent]
        if len(pool) < pick_limit:
            pool = list(source)
        random.SystemRandom().shuffle(pool)
        picked: list[dict[str, str]] = []
        seen_keys: set[str] = set()
        seen_groups: set[str] = set()
        for item in pool:
            if item["key"] in seen_keys or item.get("group") in seen_groups:
                continue
            picked.append(item)
            seen_keys.add(item["key"])
            seen_groups.add(item.get("group") or item["key"])
            if len(picked) >= pick_limit:
                break
        if len(picked) < pick_limit:
            for item in source:
                if item["key"] not in seen_keys:
                    picked.append(item)
                    seen_keys.add(item["key"])
                if len(picked) >= pick_limit:
                    break
        st.session_state.daily_art_kind = kind
        st.session_state.daily_art_pick = picked
        st.session_state[recent_key] = (recent + [item["key"] for item in picked])[-30:]

    active_kind = st.session_state.get("daily_art_kind", "portrait")
    active_source = wallpapers if active_kind == "wallpaper" else portraits
    current_pick = st.session_state.get("daily_art_pick") or []
    expected_pick_count = 1 if active_kind == "wallpaper" else min(3, len(portraits))
    active_keys = {item["key"] for item in active_source}
    if (
        not current_pick
        or not active_source
        or any(item.get("key") not in active_keys for item in current_pick)
        or (active_kind == "portrait" and len(current_pick) < expected_pick_count)
    ):
        choose_art(active_kind == "wallpaper")

    def select_art_folder(kind: str) -> None:
        if _block_readonly_action():
            _readonly_notice()
            return
        try:
            selected = daily_art.choose_source_folder(kind)
        except (OSError, RuntimeError, ValueError) as exc:
            st.error(f"无法打开或保存文件夹：{exc}")
            return
        if selected is None:
            return
        label = "竖屏" if kind == "portrait" else "壁纸"
        with st.spinner(f"正在建立{label}美图索引…"):
            daily_art.rebuild_manifest(kind)
        st.session_state.daily_art_kind = kind
        st.session_state.daily_art_pick = []
        st.session_state.pop(f"daily_art_recent_{kind}", None)
        st.toast(f"已切换{label}美图文件夹")
        st.rerun()

    with st.container(border=True, key="daily_art_panel"):
        active_kind = st.session_state.get("daily_art_kind", "portrait")
        active_source = wallpapers if active_kind == "wallpaper" else portraits
        title_col, luck_col, source_col, button_col = st.columns([.40, .14, .28, .18], vertical_alignment="center")
        with title_col:
            st.markdown('<div class="yg-art-title">今日美图</div>', unsafe_allow_html=True)
        with luck_col:
            if active_kind == "wallpaper":
                st.markdown(
                    '<div class="yg-art-luck"><span class="yg-art-rocket">🚀</span><span>运气爆棚</span></div>',
                    unsafe_allow_html=True,
                )
        with source_col:
            portrait_folder_col, wallpaper_folder_col = st.columns(2, gap="small")
            if portrait_folder_col.button(
                "竖屏", key="daily_art_choose_portrait_folder", help="重新选择竖屏美图文件夹", use_container_width=True
            ):
                select_art_folder("portrait")
            if wallpaper_folder_col.button(
                "壁纸", key="daily_art_choose_wallpaper_folder", help="重新选择壁纸美图文件夹", use_container_width=True
            ):
                select_art_folder("wallpaper")
        with button_col:
            if st.button("换一组", key="daily_art_next", use_container_width=True):
                choose_art()
                st.rerun()

        if not active_source:
            st.markdown('<div class="yg-art-empty">今日美图索引为空 · 点击重新扫描图片建立本地索引</div>', unsafe_allow_html=True)
            scan_stats = manifest.get("scan_stats") or {}
            if scan_stats:
                checked = sum(int(value.get("files_checked") or 0) for value in scan_stats.values())
                supported = sum(int(value.get("supported") or 0) for value in scan_stats.values())
                accepted = sum(int(value.get("accepted") or 0) for value in scan_stats.values())
                unreadable = sum(int(value.get("unreadable") or 0) for value in scan_stats.values())
                oversized = sum(int(value.get("oversized") or 0) for value in scan_stats.values())
                st.caption(
                    f"最近扫描：检查 {checked} 个文件 · 识别图片 {supported} 张 · "
                    f"已生成 {accepted} 张 · 无法读取 {unreadable} 张 · 超过 100MB {oversized} 张"
                )
            if st.button("重新扫描图片", key="daily_art_rescan_empty", use_container_width=True):
                if _block_readonly_action():
                    _readonly_notice()
                    return
                with st.spinner("正在从竖屏与壁纸图库重新选取并压缩本地图片索引…"):
                    daily_art.rebuild_manifest()
                st.rerun()
            return

        cards = []
        active_limit = 1 if active_kind == "wallpaper" else 3
        for item in st.session_state.get("daily_art_pick", [])[:active_limit]:
            src = html.escape(str(item["src"]), quote=True)
            pos = html.escape(str(item.get("focus") or "50% 45%"), quote=True)
            cards.append(
                f'<figure class="yg-art-card {active_kind}" style="--src:url(&quot;{src}&quot;);--pos:{pos}">'
                f'<img src="{src}" loading="eager" alt=""></figure>'
            )
        grid_class = "wallpaper" if active_kind == "wallpaper" else "portrait"
        st.markdown(
            """
            <style>
            .st-key-daily_art_panel [data-testid="stVerticalBlockBorderWrapper"]{height:100%;padding:.9rem .9rem .85rem!important;border-color:#55545b!important;background:#1d1e21!important}
            .yg-art-title{font-size:24px;font-weight:900;margin:0;color:rgba(235,235,235,.9)}
            .yg-art-luck{display:inline-flex;align-items:center;justify-content:center;gap:5px;white-space:nowrap;padding:4px 9px;border:1px solid #75394c;border-radius:999px;background:#321d25;color:#ff91af;font-size:12px;font-weight:900;box-shadow:0 0 0 rgba(255,94,137,0);animation:yg-art-luck-pulse 1.8s ease-in-out infinite}
            .yg-art-rocket{display:inline-block;transform-origin:55% 55%;animation:yg-art-rocket-flight 1.35s ease-in-out infinite}
            @keyframes yg-art-rocket-flight{0%,100%{transform:translate(0,0) rotate(0deg)}35%{transform:translate(3px,-4px) rotate(-8deg)}55%{transform:translate(5px,-2px) rotate(3deg)}75%{transform:translate(2px,-1px) rotate(-3deg)}}
            @keyframes yg-art-luck-pulse{0%,100%{box-shadow:0 0 0 rgba(255,94,137,0);border-color:#75394c}50%{box-shadow:0 0 14px rgba(255,94,137,.24);border-color:#b24f70}}
            @media (prefers-reduced-motion:reduce){.yg-art-luck,.yg-art-rocket{animation:none!important}}
            .st-key-daily_art_panel{min-width:0!important;overflow:hidden}
            .st-key-daily_art_panel [data-testid="stHorizontalBlock"],.st-key-daily_art_panel [data-testid="stColumn"]{min-width:0!important}
            .yg-art-grid{display:grid;width:100%;grid-template-columns:repeat(3,minmax(0,1fr));gap:12px;align-items:center;margin-top:8px;overflow:hidden}
            .yg-art-grid.portrait{height:342px}
            .yg-art-grid.wallpaper{display:block;height:auto;min-height:300px}
            .yg-art-card{position:relative;display:grid;place-items:center;margin:0;min-width:0;overflow:hidden;border-radius:10px;background:#111;box-shadow:0 8px 20px #0006}
            .yg-art-card.portrait{width:100%;max-width:228px;height:auto;aspect-ratio:2/3;justify-self:center}
            .yg-art-card.wallpaper{width:100%;height:auto;aspect-ratio:16/9}
            .yg-art-card::before{content:"";position:absolute;inset:0;background-image:var(--src);background-size:cover;background-position:var(--pos);filter:blur(18px) brightness(.58) saturate(1.1);transform:scale(1.12);opacity:.48}
            .yg-art-card img{position:relative;z-index:1;display:block;width:100%;height:100%;object-fit:cover;object-position:var(--pos);border-radius:10px}
            </style>
            <div class="yg-art-grid """ + grid_class + """">""" + "".join(cards) + "</div>",
            unsafe_allow_html=True,
        )
        portrait_count = sum(item.get("type") == "portrait" for item in manifest["items"])
        wallpaper_count = sum(item.get("type") == "wallpaper" for item in manifest["items"])
        st.caption(
            f"本地索引 竖屏 {portrait_count} 张 · 壁纸 {wallpaper_count} 张 · 更新于 {manifest.get('updated_at') or '—'}"
        )
        if refreshing_art:
            st.caption("新版焦点缩略图正在后台更新，稍后刷新页面即可看到更稳的裁切。")
        if st.button("重新扫描图片", key="daily_art_rescan", use_container_width=True):
            if _block_readonly_action():
                _readonly_notice()
                return
            with st.spinner("正在从竖屏与壁纸图库重新选取并压缩本地图片索引…"):
                daily_art.rebuild_manifest()
            st.rerun()


def page_home() -> None:
    works = hydrated_works()
    scored = [w for w in works if w.get("score_total") is not None]
    bgm_scored = [w for w in works if w.get("bangumi_score") is not None]
    diffs = [w["score_diff"] for w in works if w.get("score_diff") is not None]
    my_average = sum(float(w["score_total"]) for w in scored) / len(scored) if scored else None
    public_average = sum(float(w["bangumi_score"]) for w in bgm_scored) / len(bgm_scored) if bgm_scored else None
    with st.container(key="home_profile_area"):
      profile_col, art_col = st.columns([.82, 1.18], gap="small", vertical_alignment="top")
      with profile_col:
        st.markdown('<div class="yg-home-kicker">YANG-GUMI / HOME</div><h1 class="yg-home-title">我的私人档案</h1><p class="yg-home-subtitle">只留下真正属于自己的观看、阅读与游玩记录。</p>', unsafe_allow_html=True)
        render_profile_summary({
            "收藏": len(works), "完成": sum(w.get("status") == "已看" for w in works),
            "在看": sum(w.get("status") in ("在看", "重看中") for w in works),
            "我的均分": fmt_score(my_average), "Bangumi": fmt_score(public_average),
            "平均差": "—" if not diffs else f"{sum(diffs) / len(diffs):+.1f}",
        })
      with art_col:
        render_daily_art()
    render_seasonal_anime_panel(works)
    render_category_overview(works, TYPES[:4])
    render_season_time_windows(works)
    if not works:
        render_empty_state("你的私人档案馆还空着", "从一部真正喜欢的作品开始：搜索 Bangumi、确认中文名，然后留下只属于你的评分。", "✦")
        c1,c2,c3 = st.columns([1,1,3])
        if c1.button("＋ 新增第一部作品", type="primary", use_container_width=True):
            st.session_state.nav_page = "新增条目"; st.rerun()
        if c2.button("查看数据管理", use_container_width=True):
            st.session_state.nav_page = "数据管理"; st.rerun()
        return
    render_section_heading("评分画像", "STATISTICS", "个人坐标")
    render_score_distribution(works)
    with st.container(key="home_recent_grid"):
        left, right = st.columns(2, gap="medium")
        with left:
            render_section_heading("最近添加", "ARCHIVE", f"{len(works)} 部收藏")
            ranking_list(sorted(works, key=lambda w:w.get("created_at") or "", reverse=True)[:4], "recent")
        with right:
            render_section_heading("最近完成", "FINISHED", "新的余韵")
            finished = [w for w in works if w.get("status") == "已看"]
            ranking_list(
                sorted(finished, key=lambda w: w.get("finish_date") or w.get("updated_at") or "", reverse=True)[:4],
                "recent_finished",
            )


def page_library() -> None:
    header("library", "条目库", "搜索、筛选、排序，并回到每一次观看与阅读的记录。")
    works = hydrated_works()
    if notice := st.session_state.pop("library_save_notice", None):
        st.success(notice)
    reset_col, count_col = st.columns([1, 4], vertical_alignment="center")
    if reset_col.button("重置筛选", use_container_width=True, key="library_reset"):
        for key in (
            "lib_query", "lib_type", "lib_status", "lib_sort", "lib_view", "lib_subtype",
            "lib_year", "lib_tags", "lib_mine", "lib_bgm", "lib_direction", "lib_abs",
            "library_page", "library_page_size",
        ):
            st.session_state.pop(key, None)
        st.rerun()
    count_col.caption(f"数据库共 {len(works)} 个条目 · 默认显示全部，包括未评分、未选择 Bangumi 数据和无标签条目")
    with st.container(key="library_filter_bar"):
        q = st.text_input("搜索条目", placeholder="搜索中文名、原名、标签、短评、角色或台词", key="lib_query")
        c1,c2,c3,c4 = st.columns([1,1,1.2,1.1])
        type_filter = c1.selectbox("类型", ["全部"] + TYPES, key="lib_type")
        status_filter = c2.selectbox("状态", ["全部"] + STATUSES, key="lib_status")
        sort_label = c3.selectbox("排序", list(LIBRARY_SORTS), key="lib_sort")
        view_mode = c4.segmented_control("显示", ["网格", "列表"], default="网格", key="lib_view") or "网格"
    years = sorted({year for w in works if (year := flt.derive_year(w)) is not None}, reverse=True)
    tags = [t["name"] for t in db.all_tags() if t.get("category") == "Bangumi"]
    with st.expander("高级筛选与排序", expanded=False):
        c1,c2,c3 = st.columns(3)
        subtype_filter = c1.selectbox("子类型", ["全部"] + SUBTYPES, key="lib_subtype")
        year_filter = c2.selectbox("年份", ["全部"] + years + ["未知年份"], key="lib_year")
        tag_filter = c3.multiselect("标签（多选为任意匹配）", tags, key="lib_tags")
        c5,c6,c7,c8 = st.columns(4)
        mine_interval = c5.selectbox("我的评分区间", ["全部"] + SCORE_INTERVALS, key="lib_mine")
        bgm_interval = c6.selectbox("Bangumi 评分区间", ["全部"] + SCORE_INTERVALS, key="lib_bgm")
        direction = c7.selectbox("评分差方向", flt.DIFF_DIRECTIONS, key="lib_direction")
        abs_interval = c8.selectbox("评分差绝对值", ["全部"] + DIFF_INTERVALS, key="lib_abs")
    matched_ids = db.search_work_ids(q)
    filtered = []
    for w in works:
        diff = w.get("score_diff")
        work_year = flt.derive_year(w)
        year_ok = year_filter == "全部" or (year_filter == "未知年份" and work_year is None) or year_filter == work_year
        if w["id"] in matched_ids and (type_filter == "全部" or w.get("type") == type_filter) and (subtype_filter == "全部" or w.get("subtype") == subtype_filter) and (status_filter == "全部" or w.get("status") == status_filter) and year_ok and flt.matches_any_tag(w, tag_filter) and score_interval(w.get("score_total"), mine_interval) and score_interval(w.get("bangumi_score"), bgm_interval) and flt.diff_direction_matches(diff, direction) and diff_interval(diff, abs_interval):
            filtered.append(w)
    st.caption(f"当前查询结果：{len(filtered)} / {len(works)} 个条目")
    if filtered:
        sorted_items = sort_library(filtered, sort_label)
        page_size = int(st.selectbox("每页", [12, 24, 36, 60], index=1, key="library_page_size"))
        filter_signature = (
            q, type_filter, status_filter, sort_label, view_mode, subtype_filter, str(year_filter),
            tuple(tag_filter), mine_interval, bgm_interval, direction, abs_interval, len(sorted_items), page_size,
        )
        if st.session_state.get("_library_page_state") != filter_signature:
            st.session_state._library_page_state = filter_signature
            st.session_state.library_page = 1
        page, start, end = render_jump_pager(
            key_prefix="library",
            total_items=len(sorted_items),
            page_size=page_size,
            current_page=int(st.session_state.get("library_page", 1)),
            top=True,
        )
        page_items = sorted_items[start:end]
        if view_mode == "网格":
            for start in range(0, len(page_items), 3):
                columns = st.columns(3)
                for column, work in zip(columns, page_items[start:start + 3]):
                    with column: work_grid_card(work, "library_grid")
        else:
            ranking_list(page_items, "library", start + 1)
        render_jump_pager(
            key_prefix="library",
            total_items=len(sorted_items),
            page_size=page_size,
            current_page=page,
            top=False,
        )
    else:
        if not works:
            render_empty_state("条目库还没有作品", "请先前往新增条目，保存后会立即出现在这里。", "＋")
        else:
            render_empty_state("当前筛选无结果", "可点击页面顶部的“重置筛选”恢复全部条目。", "⌕")


def subject_card(
    subject: dict[str, Any], key: str, bind_callback, preferred_category: str = "全部",
    action_label: str = "选用此条目",
) -> None:
    normalized = bgm.normalize_subject(subject)
    with st.container(border=True):
        image, body, action = st.columns([1,5,1.2], vertical_alignment="center")
        with image:
            images = subject.get("images") or {}
            image_url = (
                images.get("large") or images.get("common") or images.get("medium")
                or images.get("grid") or images.get("small") or subject.get("image")
                or str(ROOT/"covers"/"default.svg")
            )
            st.image(image_url, width=116)
        with body:
            display_title = subject.get("name_cn") or subject.get("name") or subject.get("title") or subject.get("original_title") or "未命名"
            original_name = subject.get("name") or subject.get("original_title") or ""
            relevance_level = subject.get("_relevance_level")
            if relevance_level:
                source_note = " · 日本来源待确认" if subject.get("_source_status") == "unknown" else ""
                st.caption(f"{bgm.RELEVANCE_LABELS.get(relevance_level, '相关条目')}{source_note}")
            st.markdown(f"#### {display_title}")
            if original_name and original_name != display_title:
                st.caption(original_name)
            rating = subject.get("rating") or {}
            inferred = bgm.infer_local_category(subject, preferred_category)
            inferred_subtype = bgm.infer_local_subtype(subject, inferred)
            st.write(f"Bangumi 原始类型 {bgm.raw_type_name(subject)} · 本地分类 {inferred} / {inferred_subtype}")
            release_date = subject.get("release_date") or subject.get("date") or ""
            year = str(release_date)[:4] if release_date else "—"
            date_label = "首播日期" if inferred == "动画" else "发售日期"
            st.write(
                f"{date_label} {release_date or '—'} · 年份 {year} · 评分 {fmt_score(rating.get('score'))} "
                f"· 评分人数 {normalized.get('bangumi_total_votes') if normalized.get('bangumi_total_votes') is not None else '—'} "
                f"· 排名 {rating.get('rank') or '—'}"
            )
            tag_names = [str(item.get("name") or "") for item in (subject.get("tags") or []) if isinstance(item, dict)]
            if tag_names:
                st.caption("标签 · " + " · ".join(tag_names[:8]))
            summary = (subject.get("summary") or "").replace("\n", " ")
            st.caption(summary[:160] + ("…" if len(summary)>160 else ""))
        with action:
            if action_label == "查看 Bangumi" and subject.get("id"):
                st.link_button(
                    "查看 Bangumi", f"https://bgm.tv/subject/{int(subject['id'])}",
                    use_container_width=True,
                )
            elif st.button(action_label, key=key, use_container_width=True):
                bind_callback(subject, normalized)


def _sorted_subject_views(subjects: list[dict[str, Any]]) -> list[tuple[str, list[dict[str, Any]]]]:
    """Stable relevant-result views; missing values always stay at the end."""
    def date_value(subject: dict[str, Any]) -> str:
        return str(subject.get("release_date") or subject.get("date") or "")

    def score_value(subject: dict[str, Any]) -> float | None:
        value = (subject.get("rating") or {}).get("score")
        return None if value in (None, "") else float(value)

    def votes_value(subject: dict[str, Any]) -> int | None:
        return bgm.rating_total_votes(subject.get("rating") or {})

    def relevance_value(subject: dict[str, Any]) -> tuple[int, float]:
        return (
            bgm.RELEVANCE_ORDER.get(subject.get("_relevance_level"), 0),
            float(subject.get("_relevance_score") or 0),
        )

    def relevance_then(items: list[dict[str, Any]], secondary, reverse_secondary: bool) -> list[dict[str, Any]]:
        groups: dict[tuple[int, float], list[dict[str, Any]]] = {}
        for subject in items:
            groups.setdefault(relevance_value(subject), []).append(subject)
        result: list[dict[str, Any]] = []
        for rank in sorted(groups, reverse=True):
            result.extend(sorted(groups[rank], key=secondary, reverse=reverse_secondary))
        return result

    dated = [subject for subject in subjects if date_value(subject)]
    undated = [subject for subject in subjects if not date_value(subject)]
    scored = [subject for subject in subjects if score_value(subject) is not None]
    unscored = [subject for subject in subjects if score_value(subject) is None]
    voted = [subject for subject in subjects if votes_value(subject) is not None]
    unvoted = [subject for subject in subjects if votes_value(subject) is None]
    return [
        ("评分人数从高到低", relevance_then(voted, votes_value, True) + unvoted),
        ("评分人数从低到高", relevance_then(voted, votes_value, False) + unvoted),
        ("年代从旧到新", relevance_then(dated, date_value, False) + undated),
        ("年代从新到旧", relevance_then(dated, date_value, True) + undated),
        ("Bangumi 评分从高到低", relevance_then(scored, score_value, True) + unscored),
        ("Bangumi 评分从低到高", relevance_then(scored, score_value, False) + unscored),
    ]


def render_subject_result_views(
    subjects: list[dict[str, Any]], key_prefix: str, bind_callback, preferred_category: str,
    action_label: str = "选用此条目",
) -> None:
    if not subjects:
        return
    strict_subjects = [subject for subject in subjects if subject.get("_relevance_level") != "possible"]
    possible_subjects = [subject for subject in subjects if subject.get("_relevance_level") == "possible"]
    if not strict_subjects and possible_subjects:
        st.info("没有严格匹配结果，下面是可能相关结果。")
    if strict_subjects:
        st.caption("当前仅显示标题严格相关的日本动画 / 漫画 / 轻小说 / 游戏条目。")
    if strict_subjects:
        views = dict(_sorted_subject_views(strict_subjects))
        order = st.selectbox("结果排序", list(views), key=f"{key_prefix}_result_order")
        st.caption(f"{order} · 先按标题相关性过滤，再在相关结果中排序 · 最多显示前 10 个结果")
        for index, subject in enumerate(views[order][:10]):
            subject_card(subject, f"{key_prefix}_{order}_{index}_{subject.get('id')}", bind_callback, preferred_category, action_label)
    if possible_subjects:
        with st.expander(f"可能相关 / 未确认日本来源（{len(possible_subjects)}）", expanded=not strict_subjects):
            for index, subject in enumerate(possible_subjects[:10]):
                subject_card(
                    subject, f"{key_prefix}_possible_{index}_{subject.get('id')}",
                    bind_callback, preferred_category, action_label,
                )


def render_work_form(existing: dict[str, Any] | None = None) -> None:
    draft = dict(existing or st.session_state.get("new_draft") or {})
    form_key = f"work_{existing['id']}" if existing else "work_new"
    auto_key = f"{form_key}_auto_score"
    if auto_key not in st.session_state:
        st.session_state[auto_key] = draft.get("score_mode") != "manual" and scoring.default_auto_score(draft)
    info_col, form_col = st.columns([.72, 1.55], gap="large")
    with info_col, st.container(border=True, key=f"{form_key}_info_panel"):
        st.image(cover_for(draft), use_container_width=True)
        st.markdown(f"## {draft.get('title') or '新条目'}")
        if draft.get("original_title") and draft.get("original_title") != draft.get("title"):
            st.caption(draft["original_title"])
        st.write(" · ".join(str(value) for value in (draft.get("type"), draft.get("subtype"), draft.get("status")) if value))
        date_label = "首播日期" if (draft.get("type") or "动画") == "动画" else "发售日期"
        st.caption(f"{date_label} {draft.get('release_date') or draft.get('bangumi_date') or '—'} · 年份 {draft.get('year') or '—'}")
        if draft.get("bangumi_id"):
            st.metric("Bangumi", fmt_score(draft.get("bangumi_score")))
            vote_text = "—" if draft.get("bangumi_total_votes") is None else f"{int(draft['bangumi_total_votes']):,}"
            st.caption(f"评分人数 {vote_text} · 排名 {draft.get('bangumi_rank') or '—'}")
        if draft.get("score_total") is not None:
            st.metric("当前综合评分", fmt_score(draft.get("score_total")))
        summary = str(draft.get("bangumi_summary") or "").strip()
        if summary:
            st.markdown("**Bangumi 简介**")
            st.caption(summary[:300] + ("…" if len(summary) > 300 else ""))
        bgm_tags = [tag.get("name") for tag in draft.get("tags", []) if tag.get("source") == "Bangumi"]
        if bgm_tags:
            st.caption("Bangumi 标签 · " + " · ".join(bgm_tags[:12]))
    with form_col, st.container(border=True, key=f"{form_key}_form_panel"):
        c1,c2 = st.columns(2)
        title = c1.text_input("作品名 *", value=draft.get("title") or "")
        original_title = c2.text_input("原名", value=draft.get("original_title") or "")
        c1,c2,c3 = st.columns(3)
        work_type = c1.selectbox("类型", TYPES, index=TYPES.index(draft.get("type")) if draft.get("type") in TYPES else 0)
        subtype = c2.selectbox("子类型", SUBTYPES, index=SUBTYPES.index(draft.get("subtype")) if draft.get("subtype") in SUBTYPES else 0)
        status = c3.selectbox("状态", STATUSES, index=STATUSES.index(draft.get("status")) if draft.get("status") in STATUSES else 0, format_func=lambda value: "抛弃" if value == "弃置" else value)
        c1,c2 = st.columns([2,1])
        release_date = c1.text_input("首播日期" if work_type == "动画" else "发售日期", value=draft.get("release_date") or "", placeholder="YYYY-MM-DD")
        year = c2.number_input("年份", min_value=0, max_value=2200, value=int(draft.get("year") or 0), step=1)
        # The historical fields remain in SQLite, but are deliberately hidden and preserved.
        start_date = draft.get("start_date")
        finish_date = draft.get("finish_date")
        st.subheader("我的评分")
        score_config = scoring.load_score_config()
        automatic = st.toggle("自动综合评分", key=auto_key, help="按作品本体、个人感受和符合条件的时代加权计算。")
        component_values: dict[str, float | None] = {}
        custom_score_values = _custom_scores(draft)
        score_groups = [
            (f"作品本体 · 最高 {scoring.score_cap('body', score_config):g} 分", list(scoring.score_weights("body", score_config))),
            (f"个人感受 · 最高 {scoring.score_cap('feeling', score_config):g} 分", list(scoring.score_weights("feeling", score_config))),
        ]
        for group_title, fields in score_groups:
            st.caption(group_title)
            labels = [(field, _score_label(field, score_config)) for field in fields]
            for start in range(0, len(labels), 3):
                cols = st.columns(3)
                for col,(field,label) in zip(cols, labels[start:start+3]):
                    current_score = custom_score_values.get(field) if field.startswith("custom_") else draft.get(field)
                    component_values[field] = col.number_input(
                        label, min_value=0.0, max_value=10.0,
                        value=float(current_score) if current_score is not None else None,
                        step=0.1, format="%.1f", placeholder="未评分", key=f"{form_key}_{field}",
                    )
        total_votes = draft.get("bangumi_total_votes")
        if scoring.should_show_special_scores(total_votes):
            st.caption(
                f"时代加权 · 最高 {scoring.score_cap('era', score_config):g} 分 · "
                f"Bangumi 评分人数 {int(total_votes):,} · 仅评分人数超过 3000 时显示；不填写则不加分。"
            )
            cols = st.columns(2)
            for col, field in zip(cols, scoring.score_weights("era", score_config)):
                current_score = custom_score_values.get(field) if field.startswith("custom_") else draft.get(field)
                component_values[field] = col.number_input(
                    _score_label(field, score_config), min_value=0.0, max_value=10.0,
                    value=float(current_score) if current_score is not None else None,
                    step=0.1, format="%.1f", placeholder="未评分", key=f"{form_key}_{field}",
                )
        else:
            for field in scoring.score_weights("era", score_config):
                component_values[field] = custom_score_values.get(field) if field.startswith("custom_") else draft.get(field)
        component_values["bangumi_score"] = draft.get("bangumi_score")
        component_values["bangumi_total_votes"] = total_votes
        penalty_cap = scoring.imbalance_penalty_cap(component_values, total_votes, score_config)
        if penalty_cap > 0:
            gap = scoring.calculate_main_score_gap(component_values, score_config) or 0.0
            st.caption(
                f"偏科惩罚 · 只看主评分最高项与最低项差值；当前相差 {gap:.1f} 分，最高扣 {penalty_cap:.1f} 分"
            )
            current_score = draft.get(scoring.IMBALANCE_PENALTY_FIELD)
            component_values[scoring.IMBALANCE_PENALTY_FIELD] = st.number_input(
                SCORE_LABELS[scoring.IMBALANCE_PENALTY_FIELD],
                min_value=0.0, max_value=10.0,
                value=float(current_score) if current_score is not None else 0.0,
                step=0.1, format="%.1f",
                help="0 分不扣，10 分扣到当前档位上限；总扣分最高只到 2 分。",
                key=f"{form_key}_{scoring.IMBALANCE_PENALTY_FIELD}",
            )
        else:
            component_values[scoring.IMBALANCE_PENALTY_FIELD] = draft.get(scoring.IMBALANCE_PENALTY_FIELD)
        automatic_total = scoring.calculate_total_score(component_values, total_votes, config=score_config)
        if automatic:
            score_total = automatic_total
            st.metric("综合评分 · 自动计算", fmt_score(score_total, "未评分"))
            breakdown = scoring.explain_score_breakdown(component_values, score_config)
            penalty_text = "—" if breakdown["imbalance_penalty"] is None else "-" + fmt_score(breakdown["imbalance_penalty"])
            st.caption(
                f"作品本体 {fmt_score(breakdown['main_score'])} · 个人感受 +{fmt_score(breakdown['bonus_score'], '0.00')} · "
                f"时代加权 {'—' if breakdown['special_score'] is None else '+' + fmt_score(breakdown['special_score'])} · "
                f"偏科惩罚 {penalty_text} · 最高 10.00"
            )
        else:
            score_total = st.number_input(
                "总评分 · 手动评分", min_value=0.0, max_value=10.0,
                value=float(draft["score_total"]) if draft.get("score_total") is not None else None,
                step=0.01, format="%.2f", placeholder="未评分", key=f"{form_key}_manual_total",
            )
        short_review = st.text_input("一句话短评", value=draft.get("short_review") or "")
        long_review = st.text_area("长评", value=draft.get("long_review") or "", height=140)
        c1,c2 = st.columns(2)
        favorite_characters = c1.text_input("喜欢的角色（仅名字）", value=draft.get("favorite_characters") or "")
        favorite_episode = c2.text_input("最喜欢的一集 / 一章 / 一段", value=draft.get("favorite_episode") or "")
        favorite_quote = st.text_input("喜欢的台词", value=draft.get("favorite_quote") or "")
        private_note = draft.get("private_note")
        preserved_private_tags = [
            (tag["name"], tag.get("category") or "其他")
            for tag in draft.get("tags", []) if tag.get("source") != "Bangumi"
        ]
        cover_url = st.text_input("自定义封面 URL", value=draft.get("cover_url") or "", disabled=bool(draft.get("bangumi_image_url")))
        upload = st.file_uploader("本地封面（仅在没有 Bangumi 封面时使用）", type=["jpg","jpeg","png","webp"], disabled=bool(draft.get("bangumi_image_url")))
        advanced = st.expander("高级本地字段")
        resource_path = advanced.text_input("本地资源路径（仅私人保存，不在详情页或公开导出显示）", value=draft.get("resource_path") or "")
        buttons = st.columns(3)
        submitted = buttons[0].button("保存条目", type="primary", use_container_width=True, key=f"{form_key}_save")
        cancel = buttons[1].button("取消", use_container_width=True, key=f"{form_key}_cancel")
        back_detail = buttons[2].button("返回详情", use_container_width=True, key=f"{form_key}_back", disabled=not bool(existing))
    if cancel or back_detail:
        st.session_state.pop("edit_id", None)
        st.session_state.nav_page = "条目详情" if existing else "条目库"
        st.rerun()
    if submitted:
        if _block_readonly_action():
            _readonly_notice()
            return
        if not title.strip(): st.error("作品名不能为空。"); return
        cover_path = draft.get("cover_path") or ""
        if upload and not draft.get("bangumi_image_url"):
            target = ROOT / "covers" / f"{date.today():%Y%m%d}-{upload.name}"
            target.write_bytes(upload.getbuffer()); cover_path = str(target)
        custom_scores_json = json.dumps(
            {field: value for field, value in component_values.items() if field.startswith("custom_") and value is not None},
            ensure_ascii=False,
            sort_keys=True,
        )
        data = {**draft, **component_values, "custom_scores_json": custom_scores_json, "score_total":score_total, "score_mode":"auto" if automatic else "manual", "title":title.strip(), "original_title":original_title.strip(), "type":work_type, "subtype":subtype, "status":status, "start_date":start_date, "finish_date":finish_date, "release_date":release_date, "year":year or None, "short_review":short_review, "long_review":long_review, "private_note":private_note, "favorite_characters":favorite_characters, "favorite_episode":favorite_episode, "favorite_quote":favorite_quote, "cover_path":cover_path, "cover_url":cover_url, "resource_path":resource_path}
        try:
            work_id = db.save_work(data, preserved_private_tags, existing.get("id") if existing else None)
            st.session_state.pop("new_draft", None); st.session_state.pop("edit_id", None)
            for key in ("lib_query", "lib_type", "lib_status", "lib_subtype", "lib_year", "lib_tags", "lib_mine", "lib_bgm", "lib_direction", "lib_abs"):
                st.session_state.pop(key, None)
            st.session_state.detail_id=work_id
            st.session_state.detail_notice="已保存，所有统计与榜单已同步更新。"
            st.session_state.nav_page="条目详情"; st.rerun()
        except Exception as exc: st.error(f"保存失败：{exc}")


def _set_search_notice(prefix: str, level: str = "", message: str = "") -> None:
    st.session_state[f"{prefix}_notice"] = (level, message)


def _render_search_notice(prefix: str) -> None:
    level, message = st.session_state.get(f"{prefix}_notice", ("", ""))
    if not message:
        return
    {"warning": st.warning, "info": st.info, "error": st.error}.get(level, st.caption)(message)


def _search_add_bangumi() -> None:
    query = (st.session_state.get("add_query") or "").strip()
    category = st.session_state.get("add_search_category") or "全部"
    if not query:
        st.session_state.add_results = []
        _set_search_notice("add_search", "warning", "请输入作品名。")
        return
    draft = st.session_state.get("new_draft") or {}
    try:
        raw_results = bgm.search_subjects_by_category(
            query, category,
            fallback_keywords=[draft.get("title"), draft.get("original_title"), draft.get("bangumi_name_cn"), draft.get("bangumi_name")],
        )
        st.session_state.add_results = bgm.rank_search_results(query, raw_results)
        if st.session_state.add_results:
            _set_search_notice("add_search")
        else:
            _set_search_notice("add_search", "info", "没有找到与标题严格相关的日本 ACGN 条目。")
    except bgm.BangumiError as exc:
        st.session_state.add_results = []
        _set_search_notice("add_search", "error", str(exc))


def _search_match_bangumi(selected: dict[str, Any]) -> None:
    query_key = f"match_query_{selected['id']}"
    category_key = f"match_search_category_{selected['id']}"
    query = (st.session_state.get(query_key) or "").strip()
    category = st.session_state.get(category_key) or "全部"
    if not query:
        st.session_state.match_results = []
        _set_search_notice("match_search", "warning", "请输入作品名。")
        return
    try:
        raw_results = bgm.search_subjects_by_category(
            query, category,
            fallback_keywords=[selected.get("title"), selected.get("original_title"), selected.get("bangumi_name_cn"), selected.get("bangumi_name")],
        )
        st.session_state.match_results = bgm.rank_search_results(query, raw_results)
        if st.session_state.match_results:
            _set_search_notice("match_search")
        else:
            _set_search_notice("match_search", "info", "没有找到与标题严格相关的日本 ACGN 条目。")
    except bgm.BangumiError as exc:
        st.session_state.match_results = []
        _set_search_notice("match_search", "error", str(exc))


def _search_public_bangumi() -> None:
    query = (st.session_state.get("bangumi_public_query") or "").strip()
    category = st.session_state.get("bangumi_public_category") or "动画"
    if not query:
        st.session_state.bangumi_public_results = []
        _set_search_notice("bangumi_public_search", "warning", "请输入作品名。")
        return
    try:
        raw_results = bgm.search_subjects_by_category(query, category)
        st.session_state.bangumi_public_results = bgm.rank_search_results(query, raw_results)
        if st.session_state.bangumi_public_results:
            _set_search_notice("bangumi_public_search")
        else:
            _set_search_notice("bangumi_public_search", "info", "没有找到与标题严格相关的日本 ACGN 条目。")
    except bgm.BangumiError as exc:
        st.session_state.bangumi_public_results = []
        _set_search_notice("bangumi_public_search", "error", str(exc))


def page_add() -> None:
    if READ_ONLY_MODE:
        header("add", "新增条目")
        _readonly_notice()
        return
    header("add", "新增条目", "可以先从 Bangumi 选用公开条目数据，也可以完全手动记录。")
    if "add_search_category" not in st.session_state:
        st.session_state.add_search_category = "动画"
    with st.expander("Bangumi 检索与选用", expanded=not bool(st.session_state.get("new_draft"))):
        st.subheader("Bangumi 检索")
        query = st.text_input(
            "作品名", key="add_query", placeholder="输入中文、日文或英文名称后按 Enter",
            on_change=_search_add_bangumi,
        )
        category = st.radio("搜索分类", SEARCH_CATEGORIES, horizontal=True, key="add_search_category")
        st.button("搜索 Bangumi", type="primary", on_click=_search_add_bangumi, key="add_search_button", use_container_width=True)
        _render_search_notice("add_search")
        def choose(subject, normalized):
            try:
                detail = bgm.get_subject(subject["id"]); db.cache_subject(subject["id"], detail)
                st.session_state.new_draft = bgm.suggested_local_fields(detail, query, category); st.session_state.add_results=[]; st.rerun()
            except bgm.BangumiError as exc: st.error(str(exc))
        render_subject_result_views(st.session_state.get("add_results", []), "add_subject", choose, category)
    st.subheader("本地记录")
    render_work_form()


def render_bangumi_public_detail(subject_id: int) -> None:
    """Read-only Bangumi subject view, including the public staff credits."""
    try:
        subject = bgm.get_subject(subject_id)
        persons = bgm.get_subject_persons(subject_id)
        characters = bgm.get_subject_characters(subject_id)
    except bgm.BangumiError as exc:
        st.error(str(exc))
        return
    normalized = bgm.normalize_subject(subject)
    title = subject.get("name_cn") or subject.get("name") or "未命名"
    original = subject.get("name") or ""
    images = subject.get("images") or {}
    with st.container(border=True, key=f"bangumi_readonly_detail_{subject_id}"):
        st.caption("BANGUMI PUBLIC DATA · READ ONLY")
        cover, body = st.columns([1, 4.5], vertical_alignment="top")
        with cover:
            st.image(images.get("large") or images.get("common") or str(ROOT / "covers" / "default.svg"), use_container_width=True)
        with body:
            st.markdown(f"## {title}")
            if original and original != title:
                st.caption(original)
            rating = subject.get("rating") or {}
            a, b, c = st.columns(3)
            a.metric("Bangumi 评分", fmt_score(rating.get("score")))
            b.metric("评分人数", f"{int(normalized.get('bangumi_total_votes') or 0):,}")
            c.metric("排名", rating.get("rank") or "—")
            st.write(f"类型 {bgm.raw_type_name(subject)} · 日期 {subject.get('date') or '—'}")
            if subject.get("summary"):
                st.write(subject["summary"])
            tags = [item.get("name") for item in subject.get("tags") or [] if isinstance(item, dict) and item.get("name")]
            if tags:
                st.caption("标签 · " + " · ".join(tags[:16]))
        info_rows = []
        for item in subject.get("infobox") or []:
            if isinstance(item, dict) and item.get("key"):
                value = item.get("value")
                if isinstance(value, list):
                    value = "、".join(str(part.get("v") if isinstance(part, dict) else part) for part in value)
                info_rows.append({"项目": item.get("key"), "内容": str(value or "")})
        if info_rows:
            with st.expander("条目信息", expanded=False):
                st.dataframe(info_rows, hide_index=True, use_container_width=True)
        st.markdown("### 制作 Staff（监督／系列构成等）")
        if persons:
            staff_rows = []
            for person in persons:
                relation = person.get("relation") or person.get("career") or "Staff"
                if isinstance(relation, list):
                    relation = "、".join(map(str, relation))
                staff_rows.append({
                    "姓名": person.get("name") or person.get("name_cn") or "—",
                    "职责": relation,
                    "人物 ID": person.get("id") or "—",
                })
            st.dataframe(staff_rows, hide_index=True, use_container_width=True, height=min(520, 38 + len(staff_rows) * 35))
        else:
            st.info("Bangumi 暂未返回 Staff 数据。")
        st.markdown("### 角色／声优")
        cast_rows = []
        for character in characters:
            actors = character.get("actors") or []
            actor_names = []
            for actor in actors:
                if isinstance(actor, dict):
                    actor_names.append(str(actor.get("name") or actor.get("name_cn") or "").strip())
            cast_rows.append({
                "角色": character.get("name") or character.get("name_cn") or "—",
                "类型": character.get("relation") or "角色",
                "声优": "、".join(name for name in actor_names if name) or "—",
            })
        if cast_rows:
            st.dataframe(cast_rows, hide_index=True, use_container_width=True, height=min(520, 38 + len(cast_rows) * 35))
        else:
            st.info("Bangumi 暂未返回角色／声优数据。")
        st.link_button("在 Bangumi 打开", f"https://bgm.tv/subject/{subject_id}")


def _ranked_preferred_category(category: str) -> str:
    return "轻小说" if category == "小说" else category


def _save_ranked_subject(item: dict[str, Any], status: str, open_editor: bool) -> None:
    if _block_readonly_action():
        return
    subject_id = int(item["id"])
    preferred_category = _ranked_preferred_category(str(item.get("category") or "动画"))
    try:
        detail = bgm.get_subject(subject_id)
        db.cache_subject(subject_id, detail)
    except bgm.BangumiError:
        detail = item.get("subject") or {
            "id": subject_id,
            "type": bgm.RANKING_SUBJECT_TYPES.get(str(item.get("category") or "动画"), 2),
            "name": item.get("original_title") or item.get("title") or "",
            "name_cn": item.get("title") or "",
            "images": {"common": item.get("image") or ""},
            "rating": {"score": item.get("score"), "rank": item.get("rank"), "total": item.get("votes")},
        }
    existing = db.get_work_by_bangumi_id(subject_id)
    if existing:
        fields = bgm.binding_fields(detail, existing.get("title") or "", existing.get("original_title") or "")
        db.update_bangumi(int(existing["id"]), fields, include_local_titles=False)
        db.update_work_status(int(existing["id"]), status)
        work_id = int(existing["id"])
    else:
        fields = bgm.suggested_local_fields(detail, item.get("title") or "", preferred_category)
        work_id = db.save_work({**fields, "status": status, "score_total": None})
    if open_editor:
        st.session_state.detail_return_page = "Bangumi"
        st.session_state.detail_id = work_id
        st.session_state.edit_id = work_id
        st.session_state.nav_page = "条目详情"
    st.rerun()


def render_bangumi_ranking_browser() -> None:
    render_section_heading("Bangumi 评分排行榜", "PUBLIC RANKING", "仅日本 ACGN · 按公开评分从高到低")
    cols = st.columns([1.5, 1, 1], vertical_alignment="bottom")
    with cols[0]:
        category = st.segmented_control("分类", list(bgm.RANKING_CATEGORY_LABELS), default="动画", key="bangumi_rank_category") or "动画"
    with cols[1]:
        page_size = int(st.selectbox("每页", [8, 12, 16, 24], index=3, key="bangumi_rank_page_size"))
    with cols[2]:
        if st.button("刷新排行榜缓存", use_container_width=True, key="bangumi_rank_refresh"):
            if _block_readonly_action():
                _readonly_notice()
                return
            bgm.clear_ranking_cache()
            st.session_state.bangumi_rank_page = 1
            st.rerun()
    previous_state = st.session_state.get("_bangumi_rank_state")
    current_state = (category, page_size)
    if previous_state != current_state:
        st.session_state._bangumi_rank_state = current_state
        st.session_state.bangumi_rank_page = 1
    ranking_capacity = int(getattr(bgm, "RANKING_MAX_ITEMS", 7200))
    max_rank_page = max(1, (ranking_capacity + page_size - 1) // page_size)
    page = max(1, min(max_rank_page, int(st.session_state.get("bangumi_rank_page", 1))))
    source_links = {
        "动画": "https://api.bgm.tv/v0/subjects?type=2&sort=rank",
        "漫画": "https://api.bgm.tv/v0/subjects?type=1&cat=1001&sort=rank",
        "小说": "https://api.bgm.tv/v0/subjects?type=1&cat=1002&sort=rank",
        "游戏": "https://api.bgm.tv/v0/subjects?type=4&cat=4001&sort=rank",
    }
    st.caption(f"数据源 · {source_links[category]} · 当前季度缓存 {bgm.ranking_quarter_key()} · 操作按钮会写入 Yang-gumi 本地评分库")
    start_index = (page - 1) * page_size
    try:
        with st.spinner("正在读取 Bangumi 公开排行榜…"):
            fetched_rows = bgm.ranked_browser_subject_window(category, start_index, page_size + 1)
    except bgm.BangumiError as exc:
        st.warning(f"Bangumi 排行榜暂时读取失败：{exc}")
        fetched_rows = []
    rows = fetched_rows[:page_size]
    if page > 1 and not rows:
        st.session_state.bangumi_rank_page = max(1, (len(fetched_rows) + page_size - 1) // page_size)
        st.rerun()
    has_next = len(fetched_rows) > page_size
    if not rows:
        render_empty_state("排行榜暂时没有可显示条目", "稍后刷新缓存，或切换分类再试。", "◎")
        return
    page, start_index, _ = render_jump_pager(
        key_prefix="bangumi_rank",
        total_items=ranking_capacity if has_next or page > 1 else len(fetched_rows),
        page_size=page_size,
        current_page=page,
        max_jump_rank=ranking_capacity,
        top=True,
    )
    _precache_bangumi_rank_covers(rows)

    local_by_bangumi = {
        int(work["bangumi_id"]): work for work in works_snapshot()
        if work.get("bangumi_id") not in (None, "")
    }
    for item in rows:
        item["category"] = category
    for start in range(0, len(rows), 4):
        columns = st.columns(4)
        for column, item in zip(columns, rows[start:start + 4]):
            subject_id = int(item["id"])
            local = local_by_bangumi.get(subject_id) or {}
            status = local.get("status")
            with column:
                with st.container(border=True, key=f"bangumi_rank_card_{category}_{subject_id}"):
                    image_url = _bangumi_rank_cover_display_url(subject_id, item.get("image") or "")
                    st.markdown(_bangumi_cover_html(image_url, item.get("title") or ""), unsafe_allow_html=True)
                    title = html.escape(str(item.get("title") or "未命名"))
                    original = html.escape(str(item.get("original_title") or ""))
                    original_line = original if original and original != title else "&nbsp;"
                    votes = item.get("votes")
                    meta = f"BGM {fmt_score(item.get('score'))} · {int(votes):,} 人评分" if votes else f"BGM {fmt_score(item.get('score'))}"
                    st.markdown(
                        f'<div class="yg-bgm-rank-copy">'
                        f'<strong>#{item.get("rank") or "—"} · {title}</strong>'
                        f'<small>{original_line}</small>'
                        f'<b>{html.escape(meta)}</b>'
                        f'</div>',
                        unsafe_allow_html=True,
                    )
                    buttons = st.columns(3)
                    if buttons[0].button("♡", key=f"bgm_rank_seen_{'active' if status == '已看' else 'idle'}_{subject_id}", help="已看并进入评分", use_container_width=True):
                        _save_ranked_subject(item, "已看", True)
                    watch_label = "●" if status == "在看" else "○"
                    if buttons[1].button(watch_label, key=f"bgm_rank_watch_{'active' if status == '在看' else 'idle'}_{subject_id}", help="标记为已追番", use_container_width=True):
                        _save_ranked_subject(item, "在看", False)
                    if buttons[2].button("×", key=f"bgm_rank_abandon_{'active' if status == '弃置' else 'idle'}_{subject_id}", help="抛弃并进入评分", use_container_width=True):
                        _save_ranked_subject(item, "弃置", True)
                    status_line = "已追番" if status == "在看" else "已看" if status == "已看" else "已抛弃" if status == "弃置" else "&nbsp;"
                    st.markdown(f'<div class="yg-bgm-rank-status">{status_line}</div>', unsafe_allow_html=True)
    render_jump_pager(
        key_prefix="bangumi_rank",
        total_items=ranking_capacity if has_next or page > 1 else len(fetched_rows),
        page_size=page_size,
        current_page=page,
        max_jump_rank=ranking_capacity,
        top=False,
    )


def page_match() -> None:
    header("match", "Bangumi", "只读查看 Bangumi 公开评分、排名、标签与条目信息。")
    query_hint_id = st.session_state.get("match_work_id")
    query_hint = db.get_work(query_hint_id) if query_hint_id else None
    if query_hint:
        category = query_hint.get("type") if query_hint.get("type") in TYPES else "动画"
        query_key = f"match_query_{query_hint['id']}"
        category_key = f"match_search_category_{query_hint['id']}"
        if category_key not in st.session_state:
            st.session_state[category_key] = category
        st.text_input(
            "搜索关键词", value=query_hint.get("title") or "", key=query_key,
            placeholder="输入中文、日文或英文作品名后按 Enter",
            on_change=_search_match_bangumi, args=(query_hint,),
        )
        selected_category = st.radio("搜索分类", SEARCH_CATEGORIES, horizontal=True, key=category_key)
        st.button("搜索 Bangumi", type="primary", on_click=_search_match_bangumi, args=(query_hint,), key=f"match_search_button_{query_hint['id']}", use_container_width=True)
        _render_search_notice("match_search")
        render_subject_result_views(
            st.session_state.get("match_results", []), f"match_{query_hint['id']}", lambda *_: None, selected_category,
            "查看 Bangumi",
        )
        return
    render_bangumi_ranking_browser()
    with st.expander("公开条目搜索", expanded=False):
        st.caption("只读查询 Bangumi 公开条目；需要评分时请使用排行榜卡片上的按钮写入本地。")
        if "bangumi_public_category" not in st.session_state:
            st.session_state.bangumi_public_category = "动画"
        query = st.text_input(
            "搜索关键词", key="bangumi_public_query",
            placeholder="输入中文、日文或英文作品名后按 Enter", on_change=_search_public_bangumi,
        )
        category = st.radio("搜索分类", SEARCH_CATEGORIES, horizontal=True, key="bangumi_public_category")
        st.button("搜索 Bangumi", type="primary", on_click=_search_public_bangumi, key="bangumi_public_search_button", use_container_width=True)
        _render_search_notice("bangumi_public_search")
        render_subject_result_views(
            st.session_state.get("bangumi_public_results", []), "bangumi_public", lambda *_: None, category,
            "查看 Bangumi",
        )


def page_detail() -> None:
    work_id=st.session_state.get("detail_id")
    work=db.get_work(work_id) if work_id else None
    if not work: header("library", "条目详情"); st.info("请从条目库选择作品。"); return
    work["score_diff"]=calc_diff(work)
    if not READ_ONLY_MODE and st.session_state.get("edit_id")==work_id:
        header("library", f"编辑 · {work['title']}"); render_work_form(work); return
    header("library", work["title"], work.get("original_title") or "")
    if st.session_state.pop("detail_notice", None):
        st.success("已保存，所有统计与榜单已同步更新。")
    cover,main=st.columns([1.2,4.8])
    with cover: st.image(cover_for(work), use_container_width=True)
    with main:
        st.subheader("评分概览")
        a,b,c,d=st.columns(4)
        a.metric("我的总评分",fmt_score(work.get("score_total"))); b.metric("Bangumi",fmt_score(work.get("bangumi_score")))
        c.metric("评分差","—" if work.get("score_diff") is None else f"{work['score_diff']:+.1f}"); d.metric("Bangumi 排名",work.get("bangumi_rank") or "—")
        st.caption(diff_label(work.get("score_diff")))
        votes = work.get("bangumi_total_votes")
        st.caption(f"Bangumi {fmt_score(work.get('bangumi_score'))} · 评分人数 {'—' if votes is None else f'{int(votes):,}'} · 排名 {work.get('bangumi_rank') or '—'}")
        shown_status = "抛弃" if work.get("status") == "弃置" else work.get("status")
        st.write(" · ".join(x for x in [work.get("type"),work.get("subtype"),shown_status,str(work.get("year") or "")] if x))
        date_label = "首播日期" if work.get("type") == "动画" else "发售日期"
        st.caption(f"{date_label} {work.get('release_date') or '—'} · Bangumi 日期 {work.get('bangumi_date') or '—'}")
        bangumi_tags = [tag["name"] for tag in work.get("tags", []) if tag.get("source") == "Bangumi"]
        if bangumi_tags: st.caption("Bangumi 标签 · " + " · ".join(bangumi_tags))
    if work.get("short_review"): st.info(work["short_review"])
    st.subheader("分项评分")
    score_config = scoring.load_score_config()
    detail_score_fields = list(scoring.score_weights("body", score_config)) + list(scoring.score_weights("feeling", score_config))
    if scoring.should_show_special_scores(work.get("bangumi_total_votes")):
        detail_score_fields += list(scoring.score_weights("era", score_config))
    if scoring.imbalance_penalty_cap(work, work.get("bangumi_total_votes"), score_config) > 0 or work.get(scoring.IMBALANCE_PENALTY_FIELD) is not None:
        detail_score_fields.append(scoring.IMBALANCE_PENALTY_FIELD)
    cols=st.columns(5)
    for i,field in enumerate(detail_score_fields):
        label = SCORE_LABELS[field] if field == scoring.IMBALANCE_PENALTY_FIELD else _score_label(field, score_config)
        cols[i%5].metric(label, fmt_score(_work_score_value(work, field)))
    breakdown = scoring.explain_score_breakdown(work, score_config)
    st.subheader("评分拆解")
    b1,b2,b3,b4,b5 = st.columns(5)
    b1.metric("作品本体", fmt_score(breakdown["main_score"]))
    b2.metric(f"个人感受（最高 {scoring.score_cap('feeling', score_config):g}）", f"+{fmt_score(breakdown['bonus_score'], '0.00')}")
    b3.metric(f"时代加权（最高 {scoring.score_cap('era', score_config):g}）", "—" if breakdown["special_score"] is None else f"+{fmt_score(breakdown['special_score'])}")
    b4.metric("偏科惩罚", "—" if breakdown["imbalance_penalty"] is None else f"-{fmt_score(breakdown['imbalance_penalty'])}")
    b5.metric("综合评分", fmt_score(work.get("score_total")))
    if work.get("bangumi_summary"): st.subheader("Bangumi 简介"); st.write(work["bangumi_summary"])
    if work.get("long_review"): st.subheader("我的长评"); st.write(work["long_review"])
    details={"喜欢的角色":work.get("favorite_characters"),"最喜欢的一集 / 一章 / 一段":work.get("favorite_episode"),"喜欢的台词":work.get("favorite_quote"),"开始日期":work.get("start_date"),"完成日期":work.get("finish_date"),"评分模式":"自动" if work.get("score_mode")=="auto" else "手动" if work.get("score_mode")=="manual" else "旧记录", "添加时间":work.get("created_at"),"更新时间":work.get("updated_at"),"Bangumi 最后同步":work.get("bangumi_last_sync")}
    st.subheader("记录")
    for label,value in details.items():
        if value: st.markdown(f"**{label}：** {value}")
    st.divider()
    if READ_ONLY_MODE:
        cols = st.columns(2)
        if cols[0].button("返回条目库", use_container_width=True):
            st.session_state.nav_page = "条目库"
            st.rerun()
        if work.get("bangumi_url"):
            cols[1].link_button("打开 Bangumi", work["bangumi_url"], use_container_width=True)
        return
    cols=st.columns(6)
    if cols[0].button("编辑条目",use_container_width=True): st.session_state.edit_id=work_id; st.rerun()
    if work.get("bangumi_id"):
        if cols[1].button("刷新 Bangumi 数据",use_container_width=True):
            try:
                detail=bgm.get_subject(work["bangumi_id"]); db.cache_subject(work["bangumi_id"],detail)
                db.update_bangumi(work_id,bgm.binding_fields(detail, work.get("title") or "", work.get("original_title") or ""), include_local_titles=False); st.success("已刷新 Bangumi 字段；个人评分、评论、标签、状态与本地修正均未覆盖。"); st.rerun()
            except bgm.BangumiError as exc: st.error(str(exc))
        if cols[2].button("移除 Bangumi 数据",use_container_width=True): db.unbind_bangumi(work_id); st.rerun()
        if cols[3].button("查询 Bangumi 公开数据",use_container_width=True): st.session_state.nav_page="Bangumi"; st.rerun()
    else:
        if cols[1].button("查询 Bangumi 公开数据",use_container_width=True): st.session_state.nav_page="Bangumi"; st.rerun()
    if cols[4].button("返回条目库",use_container_width=True): st.session_state.nav_page="条目库"; st.rerun()
    if work.get("bangumi_url"):
        cols[5].link_button("打开 Bangumi", work["bangumi_url"], use_container_width=True)
    return_page = st.session_state.get("detail_return_page")
    if return_page and return_page not in {"条目详情", "条目库"}:
        if st.button(f"← 返回上一页（{return_page}）", key=f"detail_back_{work_id}"):
            st.session_state.nav_page = return_page
            st.rerun()
    if work.get("bangumi_id"):
        with st.expander("本地标题与日期同步选项", expanded=False):
            st.caption("普通刷新只更新 Bangumi 公共字段，不覆盖你的本地修正。只有在这里明确确认后，才采用 Bangumi 的标题、原名、日期和年份。")
            adopt = st.checkbox("我确认采用 Bangumi 标题与日期", key=f"adopt_bgm_identity_{work_id}")
            if st.button("采用 Bangumi 标题与日期", disabled=not adopt, key=f"adopt_bgm_identity_button_{work_id}"):
                try:
                    detail = bgm.get_subject(work["bangumi_id"])
                    fields = bgm.suggested_local_fields(detail, work.get("title") or "", work.get("type") or "全部")
                    db.cache_subject(work["bangumi_id"], detail)
                    db.update_bangumi(work_id, fields, include_local_titles=False)
                    db.adopt_bangumi_identity(work_id, fields)
                    st.success("已按你的确认采用 Bangumi 标题、原名、日期与年份；评分和私人内容未改变。")
                    st.rerun()
                except bgm.BangumiError as exc:
                    st.error(str(exc))
    with st.expander("危险操作 · 删除条目", expanded=False):
        st.warning(f"你确定要删除《{work['title']}》吗？此操作不可撤销。季番缓存会保留，但会解除本地关联。")
        confirm_name = st.text_input("请输入作品名确认", key=f"delete_name_{work_id}")
        if st.button("确认删除", type="primary", disabled=confirm_name.strip() != work["title"], key=f"delete_confirm_{work_id}"):
            db.delete_work(work_id); st.session_state.detail_id=None; st.session_state.nav_page="条目库"; st.rerun()


def page_rankings() -> None:
    header("ranking", "排行榜", "把你的评分拆成不同视角，再看看哪些作品总会浮到上面。")
    c1,c2,c3,c4=st.columns([1.4,1,1,.9])
    metric=c1.selectbox("榜单类型",list(_rank_metric_map()),key="rank_metric")
    work_type=c2.selectbox("类型",["全部"]+TYPES,key="rank_type")
    limit_choice=c3.selectbox("榜单长度",[10,20,50,100,250,500,1000,"全部"],index=0,key="rank_limit")
    page_size=int(c4.selectbox("每页",[12,24,50,100],index=1,key="rank_page_size"))
    works=hydrated_works()
    if work_type!="全部": works=[w for w in works if w.get("type")==work_type]
    limit=_limit_value(limit_choice,len(works))
    ranked=sort_works(works,metric,limit)
    if ranked:
        if isinstance(limit_choice, int) and limit_choice in {10, 20, 50, 100}:
            ranking_showcase(ranked[:limit_choice], f"ranking_{work_type}_{metric}_{limit_choice}", limit_choice)
        else:
            signature=(metric, work_type, limit, page_size, len(ranked))
            if st.session_state.get("_rank_page_state") != signature:
                st.session_state._rank_page_state = signature
                st.session_state.rankings_page = 1
            page, start, end = render_jump_pager(
                key_prefix="rankings",
                total_items=len(ranked),
                page_size=page_size,
                current_page=int(st.session_state.get("rankings_page", 1)),
                top=True,
            )
            ranking_list(ranked[start:end], f"ranking_{work_type}_{metric}_{limit}", start + 1)
            render_jump_pager(
                key_prefix="rankings",
                total_items=len(ranked),
                page_size=page_size,
                current_page=page,
                top=False,
            )
    else:
        render_empty_state("暂无可排行条目", "当前筛选下没有作品。", "♛")


def _render_category_rankings_section() -> None:
    st.subheader("分类型榜单")
    c1,c2,c3,c4=st.columns([1,1.25,1,.9])
    work_type=c1.selectbox("分类",TYPES)
    metric=c2.selectbox("排序指标",["我的总评分","剧情","角色塑造","作画 / 摄影","演出","音乐 / 配音","节奏","个人偏爱","重看 / 重玩价值","情绪后劲","氛围感","Bangumi 公共评分","我比 Bangumi 高最多"])
    limit_choice=c3.selectbox("榜单长度",[10,20,50,100,250,500,1000,"全部"],index=0,key="cat_limit")
    page_size=int(c4.selectbox("每页",[12,24,50,100],index=1,key="cat_page_size"))
    works=[w for w in hydrated_works() if w.get("type")==work_type]
    limit=_limit_value(limit_choice,len(works))
    ranked=sort_works(works,metric,limit)
    signature=(work_type, metric, limit, page_size, len(ranked))
    if st.session_state.get("_cat_page_state") != signature:
        st.session_state._cat_page_state = signature
        st.session_state.category_page = 1
    st.subheader(f"{work_type} Top {limit if limit < len(works) else '全部'}")
    if ranked:
        page, start, end = render_jump_pager(
            key_prefix="category",
            total_items=len(ranked),
            page_size=page_size,
            current_page=int(st.session_state.get("category_page", 1)),
            top=True,
        )
        ranking_list(ranked[start:end], f"cat_{work_type}_{metric}_{limit}", start + 1)
        render_jump_pager(
            key_prefix="category",
            total_items=len(ranked),
            page_size=page_size,
            current_page=page,
            top=False,
        )
    else:
        render_empty_state("暂无可排行条目", "这个分类下还没有作品。", "◇")


def page_category() -> None:
    st.session_state.nav_page = "排行榜"
    page_rankings()


def page_compare() -> None:
    header("compare", "评分对比", "我的评分 − Bangumi 公共评分；差异本身也是一种口味画像。")
    works=[w for w in hydrated_works() if w.get("bangumi_id") and w.get("bangumi_score") is not None]
    mine_avg=flt.average_non_null(works,"score_total"); public_avg=flt.average_non_null(works,"bangumi_score")
    diffs = [float(w["score_diff"]) for w in works if w.get("score_diff") is not None]
    votes = [int(w["bangumi_total_votes"]) for w in works if w.get("bangumi_total_votes") is not None]
    c1,c2,c3,c4=st.columns(4)
    c1.metric("已对比条目",len(works)); c2.metric("我的平均分",fmt_score(mine_avg)); c3.metric("Bangumi 平均分",fmt_score(public_avg)); c4.metric("平均差值",flt.format_diff(flt.average_non_null(works,"score_diff")))
    c1,c2,c3,c4=st.columns(4)
    c1.metric("我高于 Bangumi",sum(d > .5 for d in diffs)); c2.metric("我低于 Bangumi",sum(d < -.5 for d in diffs))
    c3.metric("基本一致",sum(abs(d) <= .5 for d in diffs)); c4.metric("平均评分人数","—" if not votes else f"{sum(votes)//len(votes):,}")
    board_options = ["我比 Bangumi 高最多", "我比 Bangumi 低最多", "我和 Bangumi 最一致", "Bangumi 高分但我个人无感", "Bangumi 一般但我很喜欢"]
    c1,c2,c3,c4=st.columns([1.35, .8, .8, 1.25])
    board_mode=c1.selectbox("榜单",["我的榜单"]+board_options,key="compare_board_mode")
    type_filter=c2.selectbox("类型",["全部"]+TYPES,key="compare_type")
    status_filter=c3.selectbox("状态",["全部"]+STATUSES,key="compare_status")
    tag_filter=c4.multiselect("标签（任意匹配）",[t["name"] for t in db.all_tags() if t.get("category") == "Bangumi"],key="compare_tags")
    with st.expander("评分区间、差值与排序",expanded=False):
        c1,c2,c3,c4=st.columns(4)
        mine_interval=c1.selectbox("我的评分区间",["全部"]+SCORE_INTERVALS,key="compare_mine")
        bgm_interval=c2.selectbox("Bangumi 评分区间",["全部"]+SCORE_INTERVALS,key="compare_bgm")
        direction=c3.selectbox("差值方向",flt.DIFF_DIRECTIONS,key="compare_direction")
        interval=c4.selectbox("差值绝对值",["全部"]+DIFF_INTERVALS,key="compare_interval")
        vote_filter=st.selectbox("Bangumi 评分人数",["全部","100 人以上","500 人以上","1000 人以上","3000 人以上","10000 人以上"],key="compare_votes")
        order=st.selectbox("排序",["我的评分从高到低","Bangumi 评分从高到低","评分人数从高到低","差值从高到低","差值从低到高","年代从旧到新","年代从新到旧"],key="compare_order")
    vote_thresholds={"全部":0,"100 人以上":100,"500 人以上":500,"1000 人以上":1000,"3000 人以上":3000,"10000 人以上":10000}
    filtered=[]
    for w in works:
        d=w.get("score_diff")
        if type_filter!="全部" and w.get("type")!=type_filter: continue
        if status_filter!="全部" and w.get("status")!=status_filter: continue
        if not flt.matches_any_tag(w,tag_filter): continue
        if not score_interval(w.get("score_total"),mine_interval): continue
        if not score_interval(w.get("bangumi_score"),bgm_interval): continue
        if not flt.diff_direction_matches(d,direction): continue
        if not diff_interval(d,interval): continue
        if vote_filter != "全部" and (w.get("bangumi_total_votes") is None or int(w["bangumi_total_votes"]) < vote_thresholds[vote_filter]): continue
        filtered.append(w)
    compare_sorts={
        "差值从高到低":("score_diff",True,False),"差值从低到高":("score_diff",False,False),
        "我的评分从高到低":("score_total",True,False),"Bangumi 评分从高到低":("bangumi_score",True,False),
        "评分人数从高到低":("bangumi_total_votes",True,False),
        "年代从旧到新":("release_date",False,False),"年代从新到旧":("release_date",True,False),
    }
    if board_mode == "我的榜单":
        field,descending,absolute=compare_sorts[order]
        result=flt.sort_null_last(filtered,field,descending,absolute=absolute)
    else:
        comparable=[w for w in filtered if w.get("score_diff") is not None]
        if board_mode=="我比 Bangumi 高最多": result=flt.sort_null_last(comparable,"score_diff",True)
        elif board_mode=="我比 Bangumi 低最多": result=flt.sort_null_last(comparable,"score_diff",False)
        elif board_mode=="我和 Bangumi 最一致": result=sorted(comparable,key=lambda w:abs(float(w["score_diff"])))
        else: result=sort_works(comparable,board_mode,len(comparable))
    st.caption(f"当前显示 {len(result)} / {len(works)} 个可比较条目")
    if board_mode == "我的榜单":
        compare_page_size=int(st.selectbox("当前结果每页", [12,24,50,100], index=1, key="compare_page_size"))
        result_limit="全部"
    else:
        c1,c2=st.columns([1,.8])
        result_limit=c1.selectbox("榜单长度",[10,20,50,100,250,500,1000,"全部"],index=0,key="compare_limit")
        compare_page_size=int(c2.selectbox("每页",[12,24,50,100],index=1,key="compare_page_size"))
        result=result[:_limit_value(result_limit,len(result))]
    compare_signature=(board_mode,type_filter,status_filter,tuple(tag_filter),mine_interval,bgm_interval,direction,interval,vote_filter,order,result_limit,compare_page_size,len(result))
    if st.session_state.get("_compare_page_state") != compare_signature:
        st.session_state._compare_page_state = compare_signature
        st.session_state.compare_page = 1
    compare_key_prefix = f"compare_filtered_{abs(hash(repr(compare_signature)))}"
    if result:
        page, start, end = render_jump_pager(
            key_prefix="compare",
            total_items=len(result),
            page_size=compare_page_size,
            current_page=int(st.session_state.get("compare_page", 1)),
            top=True,
        )
        ranking_list(result[start:end], compare_key_prefix, start + 1)
        render_jump_pager(
            key_prefix="compare",
            total_items=len(result),
            page_size=compare_page_size,
            current_page=page,
            top=False,
        )
    else: render_empty_state("没有符合条件的条目", "当前组合筛选没有命中可比较作品。", "≋")


def page_tags() -> None:
    header("tags", "标签档案", "TAG ARCHIVE · 从标签索引进入作品预览，不再一次铺满全部标签。")
    tags = [tag for tag in db.all_tags() if tag.get("category") == "Bangumi"]
    works = [work for work in hydrated_works() if work.get("status") == "已看" or work.get("score_total") is not None]
    if not tags:
        render_empty_state("EMPTY ARCHIVE", "当前还没有标签；应用 Bangumi 数据或编辑条目后会自动建立标签档案。", "#")
        return

    def tag_kind(tag: dict[str, Any]) -> str:
        name = str(tag.get("name") or "")
        category = str(tag.get("category") or "其他")
        if category != "Bangumi":
            return category if category in {"氛围", "题材"} else "其他"
        if any(token in name for token in ("动画", "漫画", "小说", "TV", "OVA", "剧场版", "游戏")):
            return "类型"
        if any(token in name for token in STATUSES):
            return "状态"
        if any(token in name.casefold() for token in ("studio", "production", "动画工房", "骨头社", "ufotable", "madhouse", "j.c.staff")):
            return "制作公司"
        if any(token in name for token in ("治愈", "压抑", "热血", "温馨", "致郁", "轻松")):
            return "氛围"
        if any(token in name for token in ("奇幻", "科幻", "校园", "恋爱", "冒险", "悬疑", "日常", "百合", "战斗")):
            return "题材"
        if any(char.isdigit() for char in name) and any(token in name for token in ("年", "年代", "月", "季度")):
            return "年代"
        return "其他"

    work_tags = {
        work["id"]: {part.strip() for part in (work.get("tag_names") or "").split(" · ") if part.strip()}
        for work in works
    }
    for tag in tags:
        tag["source_label"] = "Bangumi"
        tag["kind_label"] = tag_kind(tag)
        tag["works"] = [work for work in works if tag["name"] in work_tags.get(work["id"], set())]
        tag["work_count"] = len(tag["works"])
        tag["my_average"] = flt.average_non_null(tag["works"], "score_total")
        tag["bangumi_average"] = flt.average_non_null(tag["works"], "bangumi_score")
        differences = [work.get("score_diff") for work in tag["works"] if work.get("score_diff") is not None]
        tag["average_diff"] = sum(differences) / len(differences) if differences else None

    st.markdown('<div class="decor-label">TAG ARCHIVE · BANGUMI</div>', unsafe_allow_html=True)
    query = st.text_input("搜索标签", key="tag_index_query", placeholder="输入标签名，例如：治愈、科幻、J.C.STAFF")
    c1, c2, c3 = st.columns([1, 1.35, .8])
    kind_filter = c1.selectbox("标签类别", ["全部", "氛围", "题材", "制作公司", "年代", "类型", "状态", "其他"], key="tag_kind_filter")
    sort_label = c2.selectbox(
        "排序", ["作品数量从高到低", "我的平均分从高到低", "Bangumi 平均分从高到低", "平均差值从高到低"],
        key="tag_index_sort",
    )
    page_size = int(c3.selectbox("每页", [12, 24, 36, 60, 100], index=1, key="tag_page_size"))

    filtered_tags = [
        tag for tag in tags if tag.get("work_count", 0) > 0
        if (not query.strip() or query.strip().casefold() in str(tag["name"]).casefold())
        and (kind_filter == "全部" or tag["kind_label"] == kind_filter)
    ]
    sort_fields = {
        "作品数量从高到低": "work_count",
        "我的平均分从高到低": "my_average",
        "Bangumi 平均分从高到低": "bangumi_average",
        "平均差值从高到低": "average_diff",
    }
    sort_field = sort_fields[sort_label]
    filtered_tags.sort(key=lambda tag: (tag.get(sort_field) is not None, float(tag.get(sort_field) or 0), tag["name"]), reverse=True)
    tag_signature=(query.strip(), kind_filter, sort_label, page_size, len(filtered_tags))
    if st.session_state.get("_tag_page_state") != tag_signature:
        st.session_state._tag_page_state = tag_signature
        st.session_state.tags_page = 1
    st.caption(f"共 {len(filtered_tags)} 个标签 · 每页 {page_size} 个")
    if filtered_tags:
        page, start, end = render_jump_pager(
            key_prefix="tags",
            total_items=len(filtered_tags),
            page_size=page_size,
            current_page=int(st.session_state.get("tags_page", 1)),
            top=True,
        )
    else:
        page, start, end = 1, 0, 0
    page_tags = filtered_tags[start:end]

    if page_tags:
        columns = st.columns(4)
        for index, tag in enumerate(page_tags):
            with columns[index % 4]:
                with st.container(border=True, key=f"tag_card_{tag['id']}"):
                    st.markdown(f"### {tag['name']}")
                    st.caption(f"{tag['source_label']} · {tag['kind_label']} · {tag['work_count']} 部")
                    previews = tag["works"][:3]
                    if previews:
                        poster_cols = st.columns(3)
                        for poster_col, work in zip(poster_cols, previews):
                            poster_col.image(cover_for(work), use_container_width=True)
                    else:
                        st.markdown('<div class="yg-tag-no-poster">NO SIGNAL</div>', unsafe_allow_html=True)
                    mine = fmt_score(tag.get("my_average")); public = fmt_score(tag.get("bangumi_average"))
                    st.markdown(
                        f'<div class="yg-tag-stats"><span>MY <b>{mine}</b></span><span>BGM <b>{public}</b></span>'
                        f'<span>DIFF <b>{flt.format_diff(tag.get("average_diff"))}</b></span></div>',
                        unsafe_allow_html=True,
                    )
                    if st.button("查看作品 →", key=f"tag_open_{tag['id']}", use_container_width=True):
                        st.session_state.selected_tag = tag["name"]
                        st.session_state.nav_page = "标签作品"
                        st.rerun()
    else:
        render_empty_state("NO SIGNAL", "没有匹配当前搜索和筛选条件的标签。", "#")

    if filtered_tags:
        render_jump_pager(
            key_prefix="tags",
            total_items=len(filtered_tags),
            page_size=page_size,
            current_page=page,
            top=False,
        )



def page_tag_works() -> None:
    selected = str(st.session_state.get("selected_tag") or "").strip()
    header("tags", f"#{selected or '标签作品'}", "TAG ARCHIVE · 只展示已经看过或已经评分的作品。")
    if st.button("← 返回标签档案", key="tag_works_back"):
        st.session_state.nav_page = "标签筛选"
        st.rerun()
    if not selected:
        render_empty_state("NO TAG", "请先从标签档案选择一个标签。", "#")
        return
    works = [
        work for work in hydrated_works()
        if (work.get("status") == "已看" or work.get("score_total") is not None)
        and selected in {part.strip() for part in (work.get("tag_names") or "").split(" · ") if part.strip()}
    ]
    c1, c2 = st.columns([1.4, .8])
    sort_label = c1.selectbox("作品排序", list(LIBRARY_SORTS), key="tag_works_sort")
    page_size = int(c2.selectbox("每页", [12, 24, 50, 100], index=1, key="tag_works_page_size"))
    selected_works = sort_library(works, sort_label)
    render_section_heading("TAG WORKS", f"#{selected}", f"{len(selected_works)} 部作品")
    if not selected_works:
        render_empty_state("NO SIGNAL", "这个标签暂时没有可预览的本地作品。", "#")
        return
    tag_works_signature=(selected, sort_label, page_size, len(selected_works))
    if st.session_state.get("_tag_works_page_state") != tag_works_signature:
        st.session_state._tag_works_page_state = tag_works_signature
        st.session_state.tag_works_page = 1
    page, start, end = render_jump_pager(
        key_prefix="tag_works",
        total_items=len(selected_works),
        page_size=page_size,
        current_page=int(st.session_state.get("tag_works_page", 1)),
        top=True,
    )
    page_works = selected_works[start:end]
    for row_start in range(0, len(page_works), 3):
        row_columns = st.columns(3)
        for column, work in zip(row_columns, page_works[row_start:row_start + 3]):
            with column:
                work_grid_card(work, f"tag_works_{selected}")
    render_jump_pager(
        key_prefix="tag_works",
        total_items=len(selected_works),
        page_size=page_size,
        current_page=page,
        top=False,
    )


def appearance_settings_panel() -> None:
    st.divider()
    st.subheader("外观设置")
    st.caption("全站背景已固定为纯深灰；这里只保留界面动画强度设置。")
    settings = ui_cfg.load_settings()
    global_settings = settings["global"]
    animation_labels = {"关闭":"off", "轻微":"light", "标准":"standard"}
    current_strength = global_settings.get("animation_strength", "light") if global_settings.get("enable_motion", True) else "off"
    if current_strength not in animation_labels.values(): current_strength = "light"
    with st.form("appearance_form"):
        animation_label = st.selectbox("动画强度", list(animation_labels), index=list(animation_labels.values()).index(current_strength))
        submitted = st.form_submit_button("保存并立即应用", type="primary", use_container_width=True)
    if submitted:
        strength = animation_labels[animation_label]
        global_settings.update({
            "enable_motion": strength != "off", "enable_hover_animation": strength != "off",
            "enable_scroll_animation": strength != "off", "animation_strength": strength,
        })
        ui_cfg.save_settings(settings)
        st.success("外观设置已保存并应用。")
        st.rerun()
    if st.button("恢复全部默认外观", key="reset_appearance"):
        ui_cfg.reset_settings(); st.success("已恢复默认外观。"); st.rerun()


def _scoring_setting_rows(config: dict[str, Any]) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    for group_key, group_label in scoring.SCORE_GROUPS.items():
        for field, weight in scoring.score_weights(group_key, config).items():
            rows.append({
                "分组": group_label,
                "上限": f"{scoring.score_item_cap(group_key, field, config):g}",
                "项目": _score_label(field, config),
                "占比": f"{weight:.0%}",
            })
    return rows


def _save_scoring_config_and_recalculate(config: dict[str, Any]) -> int:
    db.backup_database()
    scoring.save_score_config(config)
    return db.recalculate_auto_scores()


def _render_dimension_manager(group_key: str, group_label: str, config: dict[str, Any]) -> None:
    with st.expander(f"{group_label} · 添加评分维度 / 删除评分维度", expanded=False):
        inactive = scoring.inactive_builtin_fields(config)
        active_labels = scoring.score_labels(group_key, config)
        add_col, delete_col = st.columns(2)
        with add_col:
            st.caption("添加评分维度")
            builtin_options = [""] + list(inactive)
            builtin = st.selectbox(
                "从未启用项目中选择",
                builtin_options,
                format_func=lambda field: "不添加已有项目" if not field else inactive[field],
                key=f"score_add_builtin_{group_key}",
            )
            custom_label = st.text_input("或自定义新维度名称", key=f"score_add_custom_{group_key}", placeholder="例如：世界观、声优表现")
            if st.button("添加评分维度", key=f"score_add_dimension_{group_key}", use_container_width=True):
                next_config = json.loads(json.dumps(config, ensure_ascii=False))
                weights = next_config[group_key].setdefault("weights", {})
                labels = next_config[group_key].setdefault("labels", {})
                if custom_label.strip():
                    field = f"custom_{secrets.token_hex(5)}"
                    labels[field] = custom_label.strip()[:24]
                elif builtin:
                    field = builtin
                    labels[field] = scoring.FIELD_LABELS.get(field, SCORE_LABELS.get(field, field))
                else:
                    st.warning("请先选择一个已有项目，或填写自定义维度名称。")
                    return
                weights[field] = 0.10
                changed = _save_scoring_config_and_recalculate(next_config)
                st.success(f"已添加评分维度；已重新计算 {changed} 条自动评分。")
                st.rerun()
        with delete_col:
            st.caption("删除评分维度")
            delete_options = list(active_labels)
            target = st.selectbox(
                "选择要从本组移除的维度",
                delete_options,
                format_func=lambda field: active_labels.get(field, field),
                key=f"score_delete_dimension_select_{group_key}",
            )
            if st.button("删除评分维度", key=f"score_delete_dimension_{group_key}", use_container_width=True, disabled=len(delete_options) <= 1):
                next_config = json.loads(json.dumps(config, ensure_ascii=False))
                next_config[group_key].setdefault("weights", {}).pop(target, None)
                next_config[group_key].setdefault("labels", {}).pop(target, None)
                changed = _save_scoring_config_and_recalculate(next_config)
                st.success(f"已删除评分维度；已重新计算 {changed} 条自动评分。")
                st.rerun()


def page_scoring_settings() -> None:
    header("data", "评分设置", "调整自动综合评分的三大项与子项目占比。")
    if READ_ONLY_MODE:
        st.info("只读分享模式下不能修改评分设置。")
        st.dataframe(pd.DataFrame(_scoring_setting_rows(scoring.load_score_config())), hide_index=True, use_container_width=True)
        return
    config = scoring.load_score_config()
    st.caption("保存或重置后，所有“自动综合评分”的作品会按当前设置重新计算；手动总评分不会被覆盖。")
    for group_key, group_label in scoring.SCORE_GROUPS.items():
        _render_dimension_manager(group_key, group_label, config)
    with st.form("scoring_settings_form"):
        next_config = json.loads(json.dumps(config, ensure_ascii=False))
        for group_key, group_label in scoring.SCORE_GROUPS.items():
            st.subheader(group_label)
            cap_col, info_col = st.columns([1, 3], vertical_alignment="center")
            next_config[group_key]["cap"] = cap_col.number_input(
                f"{group_label}满分",
                min_value=0.1,
                max_value=10.0,
                value=float(scoring.score_cap(group_key, config)),
                step=0.1,
                format="%.1f",
                key=f"score_setting_{group_key}_cap",
            )
            info_col.caption("下面填写该组内每个子项目的占比；保存时会按相对比例参与计算。")
            fields = list(scoring.score_weights(group_key, config).items())
            next_config[group_key].setdefault("labels", {})
            for start in range(0, len(fields), 3):
                cols = st.columns(3)
                for col, (field, weight) in zip(cols, fields[start:start + 3]):
                    label = col.text_input(
                        "维度名称",
                        value=_score_label(field, config),
                        max_chars=24,
                        key=f"score_setting_label_{group_key}_{field}",
                    ).strip() or _score_label(field, config)
                    percent = col.number_input(
                        f"{label}占比 · 当前上限 {scoring.score_item_cap(group_key, field, config):g} 分",
                        min_value=0.1,
                        max_value=100.0,
                        value=float(weight) * 100.0,
                        step=0.5,
                        format="%.1f",
                        key=f"score_setting_{field}",
                    )
                    next_config[group_key]["weights"][field] = percent / 100.0
                    next_config[group_key]["labels"][field] = label
        submitted = st.form_submit_button("保存评分设置并重新计算", type="primary", use_container_width=True)
    if submitted:
        try:
            changed = _save_scoring_config_and_recalculate(next_config)
            st.success(f"评分设置已保存；已重新计算 {changed} 条自动评分。")
            st.rerun()
        except Exception as exc:
            st.error(f"保存失败：{exc}")
    if st.button("重置评分设置并重新计算", key="reset_scoring_settings", use_container_width=True):
        try:
            db.backup_database()
            scoring.reset_score_config()
            changed = db.recalculate_auto_scores()
            st.success(f"评分设置已恢复默认；已重新计算 {changed} 条自动评分。")
            st.rerun()
        except Exception as exc:
            st.error(f"重置失败：{exc}")


def page_data() -> None:
    header("data", "数据管理", "数据始终留在本地；导出和恢复都由你主动触发。")
    counts = db.table_counts()
    if READ_ONLY_MODE:
        st.info("实时只读分享 · 当前页面直接读取主人的数据库；主人保存修改后，访客页面会在约 10 秒内自动更新。")
        metrics = st.columns(4)
        for col, (label, value) in zip(metrics, [
            ("作品", counts["works"]), ("标签", counts["tags"]),
            ("标签关联", counts["work_tags"]), ("季番缓存", counts["seasonal_anime_cache"]),
        ]):
            col.metric(label, value)
        st.subheader("导出可分享数据")
        if st.button("导出只读 JSON", use_container_width=True):
            _readonly_notice()
        st.subheader("评分规则")
        st.caption("分享端展示评分结果与当前权重，但不包含私人备注、本地资源路径和维护功能。")
        st.dataframe(pd.DataFrame(_scoring_setting_rows(scoring.load_score_config())), hide_index=True, use_container_width=True)
        return
    backups = db.list_backups()
    st.caption(f"数据库路径 · {db.DB_PATH}")
    metrics = st.columns(5)
    for col, (label, value) in zip(metrics, [
        ("works", counts["works"]), ("tags", counts["tags"]), ("work_tags", counts["work_tags"]),
        ("seasonal cache", counts["seasonal_anime_cache"]), ("最近备份", datetime.fromtimestamp(backups[0].stat().st_mtime).strftime("%m-%d %H:%M") if backups else "暂无")]):
        col.metric(label, value)

    left, right = st.columns(2, gap="large")
    with left:
        st.subheader("备份与恢复")
        if st.button("一键备份数据库", type="primary", use_container_width=True):
            try:
                path = db.backup_database(); st.session_state.backup_path = str(path); st.success(f"备份成功：{path}")
            except Exception as exc: st.error(f"备份失败：{exc}")
        backups = db.list_backups()
        if backups:
            selected_backup = st.selectbox("选择 backups 中的备份", [path.name for path in backups])
            restore_confirm = st.checkbox("我确认恢复会覆盖当前数据库", key="restore_confirm")
            restore_phrase = st.text_input("再次输入 RESTORE YANGGUMI", key="restore_phrase")
            if st.button("恢复所选备份", disabled=not (restore_confirm and restore_phrase == "RESTORE YANGGUMI"), use_container_width=True):
                try:
                    safety = db.restore_backup(selected_backup)
                    st.success(f"恢复成功；恢复前快照：{safety.name}")
                    st.rerun()
                except Exception as exc: st.error(f"恢复失败：{exc}")
        st.markdown("#### 加载已保存的数据")
        uploaded_backup = st.file_uploader(
            "选择卸载前保存或从其他电脑导出的 Yang-gumi 数据库（.db）",
            type=["db"],
            key="load_saved_database",
            help="导入前会自动为当前数据库建立安全备份。",
        )
        load_confirm = st.checkbox(
            "我确认加载后会以所选备份替换当前数据",
            key="load_saved_database_confirm",
        )
        if st.button(
            "加载数据",
            disabled=uploaded_backup is None or not load_confirm,
            use_container_width=True,
        ):
            try:
                db.restore_database(uploaded_backup.getvalue())
                st.success("数据加载成功；加载前的数据库已自动保存在 backups 文件夹。")
                st.rerun()
            except Exception as exc:
                st.error(f"数据加载失败：{exc}")
    with right:
        st.subheader("导出")
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        st.download_button("导出 CSV ZIP", db.export_csv(), f"yanggumi_csv_{stamp}.zip", "application/zip", use_container_width=True)
        st.download_button("导出完整 JSON", db.export_json(False), f"yanggumi_full_{stamp}.json", "application/json", use_container_width=True)
        st.download_button("导出 public_data.json", db.export_json(True), f"public_data_{stamp}.json", "application/json", use_container_width=True)

    st.divider(); st.subheader("维护工具")
    c1, c2, c3 = st.columns(3)
    if c1.button("运行数据库健康检查", use_container_width=True):
        st.session_state.health_results = db.health_check()
    if c2.button("重建标签统计", use_container_width=True):
        try:
            db.backup_database(); count = db.rebuild_tag_statistics(); st.success(f"Bangumi 标签关联已重建，共 {count} 条；旧数据未覆盖。")
        except Exception as exc: st.error(str(exc))
    auto_count = sum(work.get("score_mode") == "auto" for work in works_snapshot())
    if c3.button(f"重新计算自动评分（{auto_count}）", use_container_width=True, disabled=auto_count == 0):
        try:
            db.backup_database(); changed = db.recalculate_auto_scores(); st.success(f"已重新计算 {changed} 条自动评分；手动评分未覆盖。")
        except Exception as exc: st.error(str(exc))
    for result in st.session_state.get("health_results", []):
        (st.success if result["ok"] else st.warning)(f"{'通过' if result['ok'] else '警告'} · {result['label']} · {result['detail']}")

    with st.expander("危险操作", expanded=False):
        orphan_count = db.orphan_tag_count()
        st.write(f"无关联标签：{orphan_count} 个")
        clean_confirm = st.checkbox("确认清理无关联标签", key="clean_orphan_confirm")
        if st.button("清理无用标签", disabled=not clean_confirm or orphan_count == 0):
            db.backup_database(); st.success(f"已删除 {db.cleanup_orphan_tags()} 个无关联标签。")
        candidates = db.test_work_candidates()
        options = {f"#{item['id']} · {item['title']}": item["id"] for item in candidates}
        selected = st.multiselect("选择测试数据", list(options))
        delete_confirm = st.checkbox("确认删除所选测试数据", key="test_delete_confirm")
        if st.button("清除所选测试数据", disabled=not(selected and delete_confirm)):
            db.backup_database(); count = db.delete_selected_works([options[label] for label in selected]); st.success(f"已删除 {count} 条测试数据。")
        syncable = [work for work in works_snapshot() if work.get("bangumi_id")]
        st.write(f"可重新同步 Bangumi：{len(syncable)} 条（只更新 Bangumi 字段）")
        sync_phrase = st.text_input("输入 SYNC BANGUMI 后执行", key="sync_all_phrase")
        if st.button("重新同步全部 Bangumi 数据", disabled=sync_phrase != "SYNC BANGUMI"):
            db.backup_database(); updated = 0; failures = []
            progress = st.progress(0)
            for index, work in enumerate(syncable, 1):
                try:
                    detail = bgm.get_subject(int(work["bangumi_id"])); db.cache_subject(int(work["bangumi_id"]), detail)
                    db.update_bangumi(int(work["id"]), bgm.binding_fields(detail, work.get("title") or "", work.get("original_title") or ""), include_local_titles=False); updated += 1
                except Exception as exc: failures.append(f"{work['title']}：{exc}")
                progress.progress(index / max(1, len(syncable)))
            st.success(f"已同步 {updated} 条；个人字段未覆盖。")
            if failures: st.warning("\n".join(failures[:10]))
    appearance_settings_panel()


PAGES={"首页":page_home,"条目库":page_library,"新增条目":page_add,"Bangumi":page_match,"排行榜":page_rankings,"评分对比":page_compare,"标签筛选":page_tags,"标签作品":page_tag_works,"评分设置":page_scoring_settings,"数据管理":page_data,"条目详情":page_detail}
NAV_ICONS={"首页":"✦","条目库":"▦","新增条目":"＋","Bangumi":"◎","排行榜":"♛","评分对比":"≋","标签筛选":"#","评分设置":"⚙","数据管理":"⚙"}
if st.session_state.get("nav_page") == "Bangumi 匹配": st.session_state.nav_page = "Bangumi"
if st.session_state.get("nav_page") == "分类型榜单": st.session_state.nav_page = "排行榜"
if "nav_page" not in st.session_state: st.session_state.nav_page="首页"


with st.sidebar:
    st.markdown("""
    <div class="yg-brand">
      <div class="yg-brand-mark"><span>Y</span><i>✦</i></div>
      <div><strong>Yang-gumi</strong><span>私人 ACGN 评分档案</span></div>
    </div>
    <div class="yg-nav-label">MY ARCHIVE</div>
    """,unsafe_allow_html=True)
    hidden_pages = {"条目详情", "标签作品"}
    # Legacy validation marker: hidden_pages.add("新增条目"). In read-only mode the button stays visible and shows a permission notice.
    visible=[p for p in PAGES if p not in hidden_pages]
    current=st.session_state.nav_page if st.session_state.nav_page in visible else "首页"
    for page in visible:
        if st.button(
            f"{NAV_ICONS[page]}　{page}", key=f"sidebar_nav_{page}",
            type="primary" if page == current else "secondary", use_container_width=True,
        ):
            if READ_ONLY_MODE and page == "新增条目":
                st.session_state.readonly_notice_pending = True
                st.session_state.nav_page = page
                st.rerun()
            st.session_state.nav_page = page
            st.session_state.pop("edit_id", None)
            st.rerun()
    sync_caption = "实时只读 · 自动同步" if READ_ONLY_MODE else "SQLite · 无登录 · 无同步"
    st.markdown(f"""
    <div class="yg-sidebar-foot">
      <span class="yg-status-dot"></span><b>LOCAL VAULT</b>
      <small>{sync_caption}</small>
    </div>
    """,unsafe_allow_html=True)
top_nav_page = "标签筛选" if st.session_state.nav_page == "标签作品" else st.session_state.nav_page
render_top_nav(top_nav_page, visible)
render_page_safely(st.session_state.nav_page)
