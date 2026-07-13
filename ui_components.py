from __future__ import annotations

import base64
import hashlib
import html
import mimetypes
import os
import secrets
import warnings
from datetime import date, datetime
from pathlib import Path
from typing import Any
from urllib.parse import quote

import streamlit as st
from PIL import Image, ImageOps

ROOT = Path(__file__).resolve().parent
PLACEHOLDER = str(ROOT / "covers" / "default.svg")
SOFT_PLACEHOLDER = str(ROOT / "static" / "placeholders" / "anime-soft.svg")
LOCAL_GALLERY_FALLBACK = str(ROOT / "backgrounds" / "local-gallery-fallback.jpg")
USER_WALLPAPER_DIR = Path(
    os.getenv("YANGGUMI_WALLPAPER_DIR")
    or Path.home() / "Pictures" / "Yang-gumi" / "Wallpaper"
)
RUNTIME_GALLERY_DIR = ROOT / "static" / "backgrounds"
BACKGROUND_MODE = "off"
BACKGROUND_INTERVAL_SECONDS = 15
SEASON_MEMORY_STEP_SECONDS = 10
SEASON_MEMORY_TRANSITION_HALF_SECONDS = 0.3
BACKGROUND_DISPLAY_MODES = {"soft", "contain", "corner", "off"}
BACKGROUND_PROFILES: dict[str, dict[str, Any]] = {
    "soft": {
        "opacity": 0.10, "overlay": 0.72, "blur": 8, "brightness": 0.38,
        "saturate": 0.62, "contrast": 0.82, "size": "cover", "position": "center center",
    },
    "contain": {
        "opacity": 0.18, "overlay": 0.60, "blur": 4, "brightness": 0.52,
        "saturate": 0.78, "contrast": 0.82, "size": "min(80vw, 1440px) auto", "position": "center center",
    },
    "corner": {
        "opacity": 0.15, "overlay": 0.56, "blur": 3, "brightness": 0.50,
        "saturate": 0.68, "contrast": 0.76, "size": "min(62vw, 1080px) auto", "position": "right bottom",
    },
    "off": {
        "opacity": 0.0, "overlay": 0.0, "blur": 0, "brightness": 1.0,
        "saturate": 1.0, "contrast": 1.0, "size": "cover", "position": "center center",
    },
}


def background_profile(mode: str | None = None) -> dict[str, Any]:
    selected = mode if mode in BACKGROUND_DISPLAY_MODES else "soft"
    return dict(BACKGROUND_PROFILES[selected])


def fmt_score(value: Any, empty: str = "—") -> str:
    return empty if value is None else f"{float(value):.2f}"


def cover_for(work: dict[str, Any]) -> str:
    for key in ("bangumi_image_url", "cover_url", "cover_path"):
        value = work.get(key)
        if value and (key != "cover_path" or Path(value).exists()):
            return value
    return PLACEHOLDER


def diff_label(diff: Any) -> str:
    if diff is None:
        return "尚未比较"
    diff = float(diff)
    if diff >= 1.0:
        return "我明显更喜欢"
    if diff <= -1.0:
        return "大众更喜欢"
    if -0.5 <= diff <= 0.5:
        return "基本一致"
    return "略有差异"


def score_badges(work: dict[str, Any]) -> str:
    mine = fmt_score(work.get("score_total"))
    bgm = fmt_score(work.get("bangumi_score"))
    diff = work.get("score_diff")
    delta = "—" if diff is None else f"{float(diff):+.1f}"
    return f"我的评分 **{mine}**　·　Bangumi **{bgm}**　·　差值 **{delta}**"


def render_top_nav(current: str, pages: list[str]) -> None:
    """Anibt-inspired compact top navigation; sidebar remains a mobile fallback."""
    highlighted = "条目库" if current == "条目详情" else current
    readonly = os.getenv("YANGGUMI_READ_ONLY", "0") == "1"
    with st.container(key="top_navigation"):
        columns = st.columns([1.45] + [1] * len(pages), gap="small", vertical_alignment="center")
        columns[0].markdown(
            '<div class="yg-top-brand"><b>YANG<span>·</span>GUMI</b><small>PRIVATE ACGN VAULT</small></div>',
            unsafe_allow_html=True,
        )
        for column, page in zip(columns[1:], pages):
            if column.button(page, key=f"top_nav_{page}", type="primary" if page == highlighted else "secondary", use_container_width=True):
                if readonly and page == "新增条目":
                    st.session_state.readonly_notice_pending = True
                    st.session_state.nav_page = page
                    st.rerun()
                st.session_state.nav_page = page
                st.session_state.pop("edit_id", None)
                st.rerun()


def render_profile_summary(stats: dict[str, Any], live_readonly: bool = False) -> None:
    """Bangumi-user-page-inspired compact identity and statistics panel."""
    items = "".join(
        f'<div><small>{html.escape(label)}</small><b>{html.escape(str(value))}</b></div>'
        for label, value in stats.items()
    )
    live_readonly = os.getenv("YANGGUMI_READ_ONLY", "0") == "1"
    mode_label = "LIVE · READ ONLY · AUTO SYNC" if live_readonly else "LOCAL · PRIVATE · NO SYNC"
    st.markdown(
        f'<section class="yg-profile"><div class="yg-profile-avatar">Y</div>'
        f'<div class="yg-profile-copy"><b>Yang-gumi</b><span>私人 ACGN 评分档案</span><small>{mode_label}</small></div>'
        f'<div class="yg-profile-stats">{items}</div></section>',
        unsafe_allow_html=True,
    )


def render_category_overview(works: list[dict[str, Any]], categories: list[str]) -> None:
    """Bangumi collection-summary inspired category cards."""
    cards = []
    for category in categories:
        items = [work for work in works if work.get("type") == category]
        scores = [float(work["score_total"]) for work in items if work.get("score_total") is not None]
        top = max(items, key=lambda work: float(work.get("score_total") or -1), default=None)
        average = f"{sum(scores) / len(scores):.2f}" if scores else "—"
        cards.append(
            f'<article><header><b>我的{html.escape(category)}</b><span>{len(items):02d}</span></header>'
            f'<div class="yg-category-counts"><span>完成 <b>{sum(work.get("status") == "已看" for work in items)}</b></span>'
            f'<span>在看 <b>{sum(work.get("status") in {"在看", "重看中"} for work in items)}</b></span>'
            f'<span>搁置 <b>{sum(work.get("status") == "搁置" for work in items)}</b></span>'
            f'<span>弃置 <b>{sum(work.get("status") == "弃置" for work in items)}</b></span></div>'
            f'<footer><span>平均 {average}</span><small>{html.escape((top or {}).get("title") or "暂无最高分作品")}</small></footer></article>'
        )
    st.markdown(f'<div class="yg-category-overview">{"".join(cards)}</div>', unsafe_allow_html=True)


def render_score_distribution(works: list[dict[str, Any]]) -> None:
    """Small Bangumi-style rating histogram without a chart dependency."""
    buckets = {score: 0 for score in range(1, 11)}
    for work in works:
        if work.get("score_total") is not None:
            buckets[max(1, min(10, round(float(work["score_total"]))))] += 1
    peak = max(buckets.values(), default=1) or 1
    bars = "".join(
        f'<div><i style="height:{max(4, count / peak * 82):.0f}%"></i><b>{score}</b><small>{count}</small></div>'
        for score, count in buckets.items()
    )
    st.markdown(f'<section class="yg-score-distribution"><header><b>评分分布</b><span>MY SCORE</span></header><div>{bars}</div></section>', unsafe_allow_html=True)


def render_home_intro(total: int, average: Any, favorite_type: str = "尚待记录") -> None:
    average_text = fmt_score(average)
    st.markdown(
        f"""
        <section class="yg-home-hero">
          <div class="yg-home-copy">
            <div class="yg-hero-eyebrow">LOCAL · PRIVATE · YOUR TASTE</div>
            <h2>把看过的世界，留成自己的坐标。</h2>
            <p>这里不追逐热度，只记录你真正喜欢、遗憾或念念不忘的作品。</p>
            <div class="yg-hero-chips">
              <span>✦ 私人评分</span><span>⌕ 中文检索</span><span>◎ Bangumi 对照</span><span>⌁ 本地 SQLite</span>
            </div>
          </div>
          <div class="yg-vault-orbit">
            <div class="yg-orbit-ring"></div>
            <div class="yg-vault-number"><small>COLLECTION</small><strong>{int(total):02d}</strong><span>已记录作品</span></div>
            <div class="yg-orbit-note yg-orbit-note-a"><b>{average_text}</b><span>平均分</span></div>
            <div class="yg-orbit-note yg-orbit-note-b"><b>{html.escape(favorite_type)}</b><span>最常记录</span></div>
          </div>
        </section>
        """,
        unsafe_allow_html=True,
    )


def render_empty_state(title: str, message: str, icon: str = "✦") -> None:
    st.markdown(
        f"""
        <div class="yg-empty-state empty-anime-state">
          <div class="yg-empty-sigil">{html.escape(icon)}</div>
          <div><small class="decor-label">EMPTY ARCHIVE · NO SIGNAL</small><strong>{html.escape(title)}</strong><p>{html.escape(message)}</p></div>
        </div>
        """,
        unsafe_allow_html=True,
    )


def render_section_heading(title: str, eyebrow: str = "ARCHIVE", meta: str = "") -> None:
    meta_html = f'<span>{html.escape(meta)}</span>' if meta else ""
    st.markdown(
        f'<div class="yg-section-heading"><div><small>{html.escape(eyebrow)}</small><h3>{html.escape(title)}</h3></div>{meta_html}</div>',
        unsafe_allow_html=True,
    )


def get_current_season_by_real_time(now: date | datetime | None = None) -> dict[str, Any]:
    """Resolve the current anime season strictly from the user's local calendar time."""
    current = now or datetime.now()
    quarter = (current.month - 1) // 3 + 1
    start_month = (quarter - 1) * 3 + 1
    return {
        "season_code": f"Q{quarter}", "season_month_label": f"{start_month}月番",
        "season_start_month": start_month, "current_year": current.year,
    }


def get_season_for_date(release_date: Any) -> dict[str, Any] | None:
    """Return season metadata only when a full year/month can be parsed."""
    raw = str(release_date or "").strip()
    try:
        year, month = (int(part) for part in raw.split("-")[:2])
        if not 1 <= month <= 12:
            return None
    except (TypeError, ValueError):
        return None
    quarter = (month - 1) // 3 + 1
    start_month = (quarter - 1) * 3 + 1
    return {"year": year, "season_code": f"Q{quarter}", "season_month_label": f"{start_month}月番", "season_start_month": start_month}


def seasonal_anime_groups(works: list[dict[str, Any]], today: date | datetime | None = None) -> list[dict[str, Any]]:
    """Group every locally recorded animation in the current real-world season."""
    current = get_current_season_by_real_time(today)
    groups: list[dict[str, Any]] = []
    for years_ago in (0, 5, 10, 20):
        target_year = current["current_year"] - years_ago
        matched: list[dict[str, Any]] = []
        for work in works:
            if work.get("type") != "动画":
                continue
            season = get_season_for_date(work.get("release_date"))
            if not season:
                continue
            if season["year"] == target_year and season["season_code"] == current["season_code"]:
                matched.append(work)
        matched.sort(key=lambda work: (
            -(float(work.get("score_total")) if work.get("score_total") is not None else -1),
            -(float(work.get("bangumi_score")) if work.get("bangumi_score") is not None else -1),
            work.get("release_date") or "9999-99-99",
        ))
        groups.append({"year": target_year, "years_ago": years_ago, "works": matched, **current})
    return groups


def current_season_anime(
    works: list[dict[str, Any]], today: date | datetime | None = None, limit: int = 5,
) -> list[dict[str, Any]]:
    current = get_current_season_by_real_time(today)
    status_priority = {"在看": 0, "重看中": 0, "想看": 1, "已看": 2}
    matched = []
    for work in works:
        season = get_season_for_date(work.get("release_date"))
        if work.get("type") != "动画" or not season:
            continue
        if season["year"] != current["current_year"] or season["season_code"] != current["season_code"]:
            continue
        if work.get("status") not in status_priority:
            continue
        matched.append(work)
    def date_rank(work: dict[str, Any]) -> int:
        digits = "".join(char for char in str(work.get("release_date") or "") if char.isdigit())[:8]
        return int(digits) if digits else 0
    matched.sort(key=lambda work: (
        status_priority.get(work.get("status"), 9),
        -(float(work["score_total"]) if work.get("score_total") is not None else -1),
        -(float(work["bangumi_score"]) if work.get("bangumi_score") is not None else -1),
        -date_rank(work),
    ))
    return matched[:limit]


def render_current_season_carousel(works: list[dict[str, Any]]) -> None:
    """Fanku-home-inspired five-poster stage using only the user's local records."""
    items = current_season_anime(works)
    season = get_current_season_by_real_time()
    if not items:
        st.markdown(
            f'<section class="yg-current-season-empty"><div><small>本季 · {season["season_month_label"]}</small>'
            '<h2>本季还没有正在看的动画</h2><p>把一部本季动画加入档案后，海报会出现在这里。</p></div><span>NO SEASON RECORD</span></section>',
            unsafe_allow_html=True,
        )
        return
    duration = len(items) * 10
    scenes, keyframes = [], []
    for active, work in enumerate(items):
        posters = []
        for index, poster in enumerate(items):
            delta = index - active
            if delta > len(items) / 2:
                delta -= len(items)
            elif delta < -len(items) / 2:
                delta += len(items)
            delta = max(-2, min(2, int(delta)))
            position_class = {-2: "m2", -1: "m1", 0: "c0", 1: "p1", 2: "p2"}[delta]
            posters.append(
                f'<img class="yg-fanku-poster yg-fanku-pos-{position_class}" '
                f'src="{html.escape(_image_src(cover_for(poster)), quote=True)}" '
                f'alt="{html.escape(poster.get("title") or "动画海报", quote=True)}">'
            )
        animation = ""
        if len(items) > 1:
            start = active / len(items) * 100
            end = (active + 1) / len(items) * 100
            name = f"yg-fanku-scene-{active}"
            keyframes.append(
                f'@keyframes {name}{{0%,{max(0,start-.8):.3f}%{{opacity:0;transform:translateX(28px);pointer-events:none}}'
                f'{start:.3f}%,{max(start,end-.8):.3f}%{{opacity:1;transform:translateX(0);pointer-events:auto}}'
                f'{end:.3f}%,100%{{opacity:0;transform:translateX(-28px);pointer-events:none}}}}'
            )
            animation = f'animation:{name} {duration}s ease-in-out infinite'
        votes = work.get("bangumi_total_votes")
        votes_text = "评分人数 —" if votes is None else f"评分人数 {int(votes):,}"
        scenes.append(
            f'<article class="yg-fanku-scene" style="{animation}"><div class="yg-fanku-copy">'
            f'<small>CURRENT SEASON · {season["season_month_label"]}</small><h2>{html.escape(work.get("title") or "未命名动画")}</h2>'
            f'<p>{html.escape(work.get("original_title") or "私人当季观看档案")}</p>'
            f'<div><b>我的评分 {fmt_score(work.get("score_total"))}</b><span>Bangumi {fmt_score(work.get("bangumi_score"))}</span><span>{votes_text}</span></div>'
            f'</div><div class="yg-fanku-stage">{"".join(posters)}</div></article>'
        )
    dots = "".join(f"<i class={'active' if i == 0 else ''}></i>" for i in range(len(items)))
    st.markdown(
        f'<style>{"".join(keyframes)}</style><section class="yg-fanku-carousel"><div class="yg-fanku-scenes">{"".join(scenes)}</div>'
        f'<footer><span>当季观看 · 每 10 秒切换</span><div>{dots}</div></footer></section>',
        unsafe_allow_html=True,
    )


def _season_memory_cover_src(work: dict[str, Any], year: int, season_code: str) -> str:
    bangumi_id = work.get("bangumi_id")
    if str(bangumi_id or "").isdigit():
        poster_dir = ROOT / "static" / "seasonal_posters" / f"{int(year)}_{season_code}"
        for poster in sorted(poster_dir.glob(f"{int(bangumi_id)}.*")):
            if poster.is_file():
                return _static_image_src(poster)
    return _image_src(cover_for(work))


def _season_memory_animation(item_index: int, item_count: int, name: str) -> tuple[str, str]:
    duration = item_count * SEASON_MEMORY_STEP_SECONDS
    transition = SEASON_MEMORY_TRANSITION_HALF_SECONDS / duration * 100
    start = item_index / item_count * 100
    end = (item_index + 1) / item_count * 100
    leave = max(0, end - transition)
    after = min(100, end + transition)
    if item_index == 0:
        keyframes = (
            f"@keyframes {name}{{0%,{leave:.3f}%{{opacity:1;transform:translateY(0);pointer-events:auto}}"
            f"{after:.3f}%,100%{{opacity:0;transform:translateY(-24%);pointer-events:none}}}}"
        )
    else:
        before = max(0, start - transition)
        enter = min(100, start + transition)
        keyframes = (
            f"@keyframes {name}{{0%,{before:.3f}%{{opacity:0;transform:translateY(24%);pointer-events:none}}"
            f"{enter:.3f}%,{leave:.3f}%{{opacity:1;transform:translateY(0);pointer-events:auto}}"
            f"{after:.3f}%,100%{{opacity:0;transform:translateY(-24%);pointer-events:none}}}}"
        )
    style = f"animation:{name} {duration}s cubic-bezier(.22,.8,.25,1) infinite"
    return keyframes, style


def render_season_time_windows(works: list[dict[str, Any]]) -> None:
    groups = seasonal_anime_groups(works)
    animation_run_id = secrets.token_hex(4)
    windows: list[str] = []
    animation_css: list[str] = []
    for group_index, group in enumerate(groups):
        ago = group["years_ago"]
        label = "当季" if ago == 0 else f"{ago} 年前"
        title = f'{group["year"]} · {group["season_month_label"]}'
        items = group["works"]
        if items:
            cards: list[str] = []
            for item_index, work in enumerate(items):
                animation_name = f"yg-season-{animation_run_id}-{group_index}-{item_index}"
                style = ""
                if len(items) > 1:
                    keyframes, style = _season_memory_animation(item_index, len(items), animation_name)
                    animation_css.append(keyframes)
                original = work.get("original_title") or ""
                review = work.get("short_review") or "暂无短评"
                record_date = work.get("finish_date") or work.get("start_date") or ""
                record_footer = f'<footer>{html.escape(record_date)}</footer>' if record_date else ""
                cover_src = _season_memory_cover_src(work, group["year"], group["season_code"])
                cards.append(
                    f'<article class="yg-season-memory" style="{style}"><img src="{html.escape(cover_src, quote=True)}" alt="{html.escape(work.get("title") or "动画海报", quote=True)}">'
                    f'<div><span>{html.escape(work.get("status") or "已记录")}</span><strong>{html.escape(work.get("title") or "未命名动画")}</strong>'
                    f'<small>{html.escape(original)}</small><p><b>我的评分 {fmt_score(work.get("score_total"))}</b><b>Bangumi {fmt_score(work.get("bangumi_score"))}</b></p>'
                    f'<em>{html.escape(review)}</em>{record_footer}</div></article>'
                )
            track = f'<div class="yg-season-memory-stack">{"".join(cards)}</div>'
        else:
            track = '<div class="yg-season-empty"><div class="yg-season-black"><span>NO RECORD</span></div><strong>这一季还没有记录动画</strong><small>去看动画吧 ✦</small></div>'
        windows.append(
            f'<section class="yg-season-window"><header><div><small>{html.escape(label)} · {group["season_code"]}</small><b>{html.escape(title)}</b></div><span>{len(items):02d}</span></header><div class="yg-season-screen">{track}</div></section>'
        )
    st.markdown(
        f'<style>{"".join(animation_css)}</style><div class="yg-season-title"><div><small>SEASONAL TIME MACHINE</small><h3>同一季度，四段观看记忆</h3></div><span>每 10 秒切换 · 仅本地动画记录</span></div>'
        f'<div class="yg-season-grid">{"".join(windows)}</div>',
        unsafe_allow_html=True,
    )


def work_row(work: dict[str, Any], key_prefix: str = "work", rank: int | None = None) -> None:
    with st.container(border=True, key=f"{key_prefix}_row_{work['id']}"):
        # Reserve enough room for the enlarged poster so the text track begins
        # clearly to its right instead of overlapping it.
        compact = key_prefix in {"recent", "recent_finished"}
        cover, body, action = st.columns([1.18, 5.22, 1.60] if compact else [1.42, 5.53, 1.05], vertical_alignment="center")
        with cover:
            st.image(cover_for(work), width=84 if compact else 96)
        with body:
            title = work.get("title") or "未命名"
            rank_html = f'<span class="yg-rank-number">#{rank:02d}</span>' if rank else ""
            st.markdown(f'<div class="yg-work-title">{rank_html}<strong>{html.escape(title)}</strong></div>', unsafe_allow_html=True)
            original = work.get("original_title") or ""
            original_text = html.escape(str(original)) if original and original != title else "&nbsp;"
            st.markdown(f'<div class="yg-work-original">{original_text}</div>', unsafe_allow_html=True)
            mine = fmt_score(work.get("score_total")); public = fmt_score(work.get("bangumi_score"))
            diff = work.get("score_diff"); delta = "—" if diff is None else f"{float(diff):+.1f}"
            delta_class = "neutral" if diff is None or abs(float(diff)) <= .5 else ("positive" if float(diff) > 0 else "negative")
            st.markdown(
                f'<div class="yg-score-row"><span><small>MY</small><b>{mine}</b></span><span><small>BGM</small><b>{public}</b></span><span class="{delta_class}"><small>DIFF</small><b>{delta}</b></span></div>',
                unsafe_allow_html=True,
            )
            votes = work.get("bangumi_total_votes")
            votes_text = "评分人数 —" if votes is None else f"评分人数 {int(votes):,}"
            st.markdown(
                f'<div class="yg-work-votes">{html.escape(votes_text)} · Bangumi 排名 {work.get("bangumi_rank") or "—"}</div>',
                unsafe_allow_html=True,
            )
            meta_values = [work.get("type"), work.get("subtype"), work.get("status")]
            tags = list(dict.fromkeys([str(v) for v in meta_values if v] + sorted((work.get("tag_names") or "").split(" · "))[:4]))
            tags_html = "".join(f'<span>{html.escape(tag)}</span>' for tag in tags if tag)
            st.markdown(f'<div class="yg-meta-pills yg-work-tags">{tags_html or "&nbsp;"}</div>', unsafe_allow_html=True)
            review = f'“{html.escape(str(work["short_review"]))}”' if work.get("short_review") else "&nbsp;"
            st.markdown(f'<div class="yg-review-line">{review}</div>', unsafe_allow_html=True)
            finish_text = f'完成于 {html.escape(str(work["finish_date"]))}' if work.get("finish_date") else "&nbsp;"
            st.markdown(f'<div class="yg-work-finish">{finish_text}</div>', unsafe_allow_html=True)
        with action:
            if st.button("打开档案 →", key=f"{key_prefix}_{work['id']}", use_container_width=True):
                st.session_state.detail_return_page = st.session_state.get("nav_page", "条目库")
                st.session_state.detail_id = work["id"]
                st.session_state.nav_page = "条目详情"
                st.rerun()


def work_grid_card(work: dict[str, Any], key_prefix: str = "grid") -> None:
    """Fanku seasonal-card-inspired horizontal poster card for three-column grids."""
    with st.container(border=True, key=f"{key_prefix}_grid_card_{work['id']}"):
        cover, body = st.columns([1.18, 1.82], vertical_alignment="top")
        with cover:
            st.image(cover_for(work), use_container_width=True)
        with body:
            title = str(work.get("title") or "未命名")
            original = str(work.get("original_title") or "")
            original_text = html.escape(original) if original and original != title else "&nbsp;"
            st.markdown(f'<div class="yg-grid-title">{html.escape(title)}</div>', unsafe_allow_html=True)
            st.markdown(f'<div class="yg-grid-original">{original_text}</div>', unsafe_allow_html=True)
            mine, public = fmt_score(work.get("score_total")), fmt_score(work.get("bangumi_score"))
            diff = work.get("score_diff")
            delta = "—" if diff is None else f"{float(diff):+.1f}"
            st.markdown(
                f'<div class="yg-score-row"><span><small>MY</small><b>{mine}</b></span>'
                f'<span><small>BGM</small><b>{public}</b></span><span><small>DIFF</small><b>{delta}</b></span></div>',
                unsafe_allow_html=True,
            )
            votes = work.get("bangumi_total_votes")
            st.markdown(
                f'<div class="yg-grid-votes">评分人数 {"—" if votes is None else f"{int(votes):,}"} · 排名 {work.get("bangumi_rank") or "—"}</div>',
                unsafe_allow_html=True,
            )
            values = list(dict.fromkeys(
                [str(item) for item in [work.get("type"), work.get("subtype"), work.get("status")] if item]
                + [item for item in (work.get("tag_names") or "").split(" · ") if item]
            ))[:6]
            st.markdown(
                '<div class="yg-meta-pills yg-grid-tags">' + ("".join(f"<span>{html.escape(str(v))}</span>" for v in values if v) or "&nbsp;") + "</div>",
                unsafe_allow_html=True,
            )
            review = html.escape(str(work["short_review"])) if work.get("short_review") else "&nbsp;"
            st.markdown(f'<div class="yg-grid-review">{review}</div>', unsafe_allow_html=True)
        if st.button("打开档案 →", key=f"{key_prefix}_{work['id']}", use_container_width=True):
            st.session_state.detail_return_page = st.session_state.get("nav_page", "条目库")
            st.session_state.detail_id = work["id"]
            st.session_state.nav_page = "条目详情"
            st.rerun()


def ranking_list(works: list[dict[str, Any]], key_prefix: str, start_rank: int = 1) -> None:
    if not works:
        render_empty_state("这里暂时没有条目", "试试调整筛选条件，或先把一部喜欢的作品加入私人档案。", "☆")
        return
    for index, work in enumerate(works, 1):
        work_row(work, key_prefix, start_rank + index - 1)


def ranking_showcase(works: list[dict[str, Any]], key_prefix: str, limit: int) -> None:
    """Four visibly different tiers: hall of fame, selection, gallery and archive."""
    if not works:
        render_empty_state("这里暂时没有条目", "先给作品评分，榜单就会在这里形成。", "☆")
        return
    if limit >= 100:
        st.markdown('<div class="yg-ranking-tier-title tier-100">TOP 100 · 完整档案榜</div>', unsafe_allow_html=True)
        ranking_list(works, key_prefix)
        return

    if limit == 50:
        st.markdown('<div class="yg-ranking-tier-title tier-50">TOP 50 · 双列竞技榜</div>', unsafe_allow_html=True)
        cols = st.columns(2, gap="medium")
        for index, work in enumerate(works, 1):
            with cols[(index - 1) % 2]:
                with st.container(border=True, key=f"ranking_fifty_card_{index}_{work['id']}"):
                    poster, info = st.columns([.72, 2.28], vertical_alignment="center")
                    with poster:
                        st.image(cover_for(work), use_container_width=True)
                    with info:
                        st.markdown(
                            f'<div class="yg-fifty-rank">#{index:02d}</div><h3>{html.escape(work.get("title") or "未命名")}</h3>',
                            unsafe_allow_html=True,
                        )
                        st.caption(work.get("original_title") or "")
                        st.markdown(
                            f'<div class="yg-fifty-score"><b>{fmt_score(work.get("score_total"))}</b><span>MY SCORE</span>'
                            f'<em>BGM {fmt_score(work.get("bangumi_score"))}</em></div>',
                            unsafe_allow_html=True,
                        )
                        if st.button("档案 →", key=f"{key_prefix}_fifty_{work['id']}", use_container_width=True):
                            st.session_state.detail_return_page = st.session_state.get("nav_page", "排行榜")
                            st.session_state.detail_id = work["id"]
                            st.session_state.nav_page = "条目详情"
                            st.rerun()
        return

    if limit == 20:
        st.markdown('<div class="yg-ranking-tier-title tier-20">TOP 20 · 编辑精选海报墙</div>', unsafe_allow_html=True)
        cols = st.columns(4, gap="medium")
        for index, work in enumerate(works, 1):
            with cols[(index - 1) % 4], st.container(border=True, key=f"ranking_twenty_card_{index}_{work['id']}"):
                st.markdown(f'<div class="yg-twenty-rank">NO. {index:02d}</div>', unsafe_allow_html=True)
                st.image(cover_for(work), use_container_width=True)
                st.markdown(f"### {html.escape(work.get('title') or '未命名')}")
                st.caption(work.get("original_title") or "")
                st.markdown(
                    f'<div class="yg-twenty-score"><b>{fmt_score(work.get("score_total"))}</b>'
                    f'<span>BGM {fmt_score(work.get("bangumi_score"))}</span></div>',
                    unsafe_allow_html=True,
                )
                if st.button("查看 →", key=f"{key_prefix}_twenty_{work['id']}", use_container_width=True):
                    st.session_state.detail_return_page = st.session_state.get("nav_page", "排行榜")
                    st.session_state.detail_id = work["id"]
                    st.session_state.nav_page = "条目详情"
                    st.rerun()
        return

    leaders = works[:3]
    order = [1, 0, 2] if len(leaders) >= 3 else list(range(len(leaders)))
    labels = {0: "冠军", 1: "亚军", 2: "季军"}
    with st.container(key=f"ranking_podium_{limit}"):
        st.markdown('<div class="yg-podium-kicker">PERSONAL TOP · HALL OF FAME</div>', unsafe_allow_html=True)
        columns = st.columns([1, 1.18, 1][:len(order)], vertical_alignment="bottom")
        for column, leader_index in zip(columns, order):
            work = leaders[leader_index]
            rank = leader_index + 1
            with column, st.container(border=True, key=f"podium_card_{limit}_{rank}_{work['id']}"):
                st.markdown(f'<div class="yg-podium-medal rank-{rank}"><b>{rank}</b><span>{labels[leader_index]}</span></div>', unsafe_allow_html=True)
                with st.container(key=f"podium_image_{limit}_{rank}_{work['id']}"):
                    st.image(cover_for(work), use_container_width=True)
                st.markdown(f"### {html.escape(work.get('title') or '未命名')}")
                if work.get("original_title") and work.get("original_title") != work.get("title"):
                    st.caption(work["original_title"])
                st.markdown(f'<div class="yg-podium-score">{fmt_score(work.get("score_total"))}<small>MY SCORE</small></div>', unsafe_allow_html=True)
                if st.button("进入档案 →", key=f"{key_prefix}_podium_{work['id']}", use_container_width=True):
                    st.session_state.detail_return_page = st.session_state.get("nav_page", "排行榜")
                    st.session_state.detail_id = work["id"]
                    st.session_state.nav_page = "条目详情"
                    st.rerun()

    remainder = works[3:]
    if remainder:
        st.markdown('<div class="yg-ranking-tier-title tier-10">TOP 10 · 星光长廊</div>', unsafe_allow_html=True)
        cols = st.columns(4, gap="medium")
        for index, work in enumerate(remainder, 4):
            with cols[(index - 4) % 4], st.container(border=True, key=f"ranking_ten_card_{index}_{work['id']}"):
                st.markdown(f'<div class="yg-ten-rank">#{index:02d}</div>', unsafe_allow_html=True)
                st.image(cover_for(work), use_container_width=True)
                st.markdown(f"### {html.escape(work.get('title') or '未命名')}")
                st.markdown(
                    f'<div class="yg-ten-score">{fmt_score(work.get("score_total"))}<small>PERSONAL SCORE</small></div>',
                    unsafe_allow_html=True,
                )
                if st.button("进入档案 →", key=f"{key_prefix}_ten_{work['id']}", use_container_width=True):
                    st.session_state.detail_return_page = st.session_state.get("nav_page", "排行榜")
                    st.session_state.detail_id = work["id"]
                    st.session_state.nav_page = "条目详情"
                    st.rerun()


def _image_src(value: str | None) -> str:
    if not value:
        return ""
    if value.startswith(("http://", "https://", "data:")):
        return value
    path = Path(value)
    if not path.exists() or not path.is_file():
        return ""
    mime = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
    return f"data:{mime};base64,{base64.b64encode(path.read_bytes()).decode('ascii')}"


def _static_image_src(path: Path) -> str:
    """Return Streamlit's cacheable static URL for a file inside the app static directory."""
    try:
        relative = path.resolve().relative_to((ROOT / "static").resolve()).as_posix()
    except ValueError:
        return _image_src(str(path))
    return f"/app/static/{quote(relative)}"


def _local_gallery_sources(limit: int = 5) -> list[str]:
    """Randomly sample the whole local wallpaper library once per Streamlit session."""
    supported = {".jpg", ".jpeg", ".png", ".webp", ".bmp"}
    if not USER_WALLPAPER_DIR.exists():
        return []
    candidates = sorted(path for path in USER_WALLPAPER_DIR.iterdir() if path.is_file() and path.suffix.lower() in supported)
    if not candidates:
        return []
    session_key = "yg_random_landscape_wallpaper_paths_v2"
    source_key = "yg_random_landscape_wallpaper_sources_v2"
    if prepared := st.session_state.get(source_key):
        return list(prepared)[:limit]
    selected_paths = st.session_state.get(session_key)
    if not selected_paths:
        selected_paths = [str(path) for path in secrets.SystemRandom().sample(candidates, len(candidates))]
        st.session_state[session_key] = selected_paths
    RUNTIME_GALLERY_DIR.mkdir(parents=True, exist_ok=True)
    sources: list[str] = []
    for raw_path in selected_paths:
        path = Path(raw_path)
        if not path.exists():
            continue
        fingerprint = hashlib.sha1(f"16x10-v2|{path}|{path.stat().st_mtime_ns}".encode("utf-8")).hexdigest()[:14]
        target = RUNTIME_GALLERY_DIR / f"{fingerprint}.jpg"
        if not target.exists():
            try:
                with warnings.catch_warnings():
                    warnings.simplefilter("ignore", Image.DecompressionBombWarning)
                    with Image.open(path) as image:
                        if image.width * image.height > 90_000_000:
                            continue
                        if image.width <= image.height or image.width / image.height < 1.35:
                            continue
                        image = ImageOps.exif_transpose(image).convert("RGB")
                        image = ImageOps.fit(image, (1440, 900), method=Image.Resampling.LANCZOS)
                        image.save(target, "JPEG", quality=58, optimize=True)
            except (OSError, ValueError, Image.DecompressionBombError):
                continue
        if source := _static_image_src(target):
            sources.append(source)
        if len(sources) >= limit:
            break
    st.session_state[source_key] = sources
    return sources


def _background_carousel(
    sources: list[str], overlay: float, blur: int, brightness: float, fixed: str,
    display_mode: str = BACKGROUND_MODE,
) -> None:
    """Render a cached, low-presence background carousel without rerunning Streamlit."""
    selected_mode = display_mode if display_mode in BACKGROUND_DISPLAY_MODES else "soft"
    profile = background_profile(selected_mode)
    image_opacity = float(profile["opacity"])
    overlay = min(0.65, max(0.45, min(float(profile["overlay"]), overlay)))
    blur = max(int(profile["blur"]), blur)
    brightness = min(float(profile["brightness"]), brightness)
    saturate = float(profile["saturate"])
    contrast = float(profile["contrast"])
    background_size = str(profile["size"])
    background_position = str(profile["position"])
    sources = list(dict.fromkeys(source for source in sources if source))[:6]
    if selected_mode == "off" or not sources:
        return
    if len(sources) == 1:
        safe_source = html.escape(sources[0], quote=True)
        st.markdown(
            f'<div class="yg-page-bg yg-bg-single yg-bg-mode-{selected_mode}" style="position:{fixed};background-image:url(\'{safe_source}\');background-size:{background_size};background-position:{background_position};opacity:{image_opacity};filter:blur({blur}px) brightness({brightness}) saturate({saturate}) contrast({contrast})"></div>'
            f'<div class="yg-page-overlay" style="position:{fixed};background:linear-gradient(115deg,rgba(6,8,15,{min(overlay + .10, .98)}),rgba(8,11,20,{overlay}),rgba(11,10,22,{max(overlay - .08, .35)}))"></div>',
            unsafe_allow_html=True,
        )
        return

    count = len(sources)
    duration = count * BACKGROUND_INTERVAL_SECONDS
    slot = 100 / count
    fade = min(1.2, slot / 5)
    radios = ['<input class="yg-bg-radio" type="radio" name="yg-background" id="yg-bg-auto" checked>']
    radios += [f'<input class="yg-bg-radio" type="radio" name="yg-background" id="yg-bg-{index}">' for index in range(1, count + 1)]
    slides = "".join(
        f'<div class="yg-bg-slide yg-bg-slide-{index} yg-bg-mode-{selected_mode}" style="background-image:url(\'{html.escape(source, quote=True)}\');background-size:{background_size};background-position:{background_position};animation-duration:{duration}s;animation-delay:{(index - 1) * BACKGROUND_INTERVAL_SECONDS}s;filter:blur({blur}px) brightness({brightness}) saturate({saturate}) contrast({contrast})"></div>'
        for index, source in enumerate(sources, 1)
    )
    manual_rules = "".join(
        f'#yg-bg-{index}:checked ~ .yg-background-carousel .yg-bg-slides .yg-bg-slide{{animation:none;opacity:0}}'
        f'#yg-bg-{index}:checked ~ .yg-background-carousel .yg-bg-slides .yg-bg-slide-{index}{{opacity:{image_opacity}}}'
        f'#yg-bg-{index}:checked ~ .yg-bg-controls .yg-bg-control-{index}{{display:flex}}'
        for index in range(1, count + 1)
    )
    control_groups = []
    for index in range(1, count + 1):
        previous_index = count if index == 1 else index - 1
        next_index = 1 if index == count else index + 1
        control_groups.append(
            f'<div class="yg-bg-control-group yg-bg-control-{index}">'
            f'<label for="yg-bg-{previous_index}" title="上一张背景">‹</label>'
            f'<label for="yg-bg-{next_index}" title="下一张背景">›</label></div>'
        )
    dots = "".join(f'<label for="yg-bg-{index}" title="切换到第 {index} 张"></label>' for index in range(1, count + 1))
    st.markdown(
        f"""
        <style>
          @keyframes yg-bg-cycle {{
            0%, {max(slot - fade, fade):.3f}% {{opacity:{image_opacity}}}
            {slot:.3f}%, 100% {{opacity:0}}
          }}
          {manual_rules}
        </style>
        <div class="yg-background-root">
          {''.join(radios)}
          <div class="yg-background-carousel" style="position:{fixed}">
            <div class="yg-bg-slides">{slides}</div>
            <div class="yg-page-overlay" style="background:linear-gradient(115deg,rgba(6,8,15,{min(overlay + .10, .98)}),rgba(8,11,20,{overlay}),rgba(11,10,22,{max(overlay - .08, .35)}))"></div>
          </div>
          <div class="yg-bg-controls">
            <div class="yg-bg-control-group yg-bg-control-auto"><label for="yg-bg-{count}" title="上一张背景">‹</label><label for="yg-bg-2" title="下一张背景">›</label></div>
            {''.join(control_groups)}
            <div class="yg-bg-dots">{dots}</div>
            <label class="yg-bg-play" for="yg-bg-auto" title="恢复每 {BACKGROUND_INTERVAL_SECONDS} 秒自动播放">↻</label>
          </div>
        </div>
        """,
        unsafe_allow_html=True,
    )


def poster_pool(works: list[dict[str, Any]], max_count: int = 24) -> list[dict[str, Any]]:
    """Stable poster pool: watched animation first, then other recorded works."""
    valid = []
    placeholder_source = _image_src(PLACEHOLDER)
    for work in works:
        source = _image_src(cover_for(work))
        if source and source != placeholder_source:
            item = dict(work)
            item["_poster_src"] = source
            valid.append(item)
    watched = {"已看", "已完成", "想重看", "重看中"}
    def quality(work: dict[str, Any]) -> tuple[Any, ...]:
        return (float(work.get("score_total") or 0), work.get("finish_date") or "", work.get("updated_at") or "")
    primary = sorted([w for w in valid if w.get("type") == "动画" and w.get("status") in watched], key=quality, reverse=True)
    secondary = sorted([w for w in valid if w.get("type") == "动画" and w not in primary], key=quality, reverse=True)
    supplements = sorted([w for w in valid if w.get("type") != "动画"], key=quality, reverse=True)
    deduped, seen = [], set()
    for work in primary + secondary + supplements:
        source = work["_poster_src"]
        if source in seen:
            continue
        seen.add(source); deduped.append(work)
        if len(deduped) >= max(1, min(int(max_count), 24)):
            break
    return deduped


def _poster_images(posters: list[dict[str, Any]], css_class: str, limit: int) -> str:
    return "".join(
        f'<img class="{css_class}" src="{html.escape(work["_poster_src"], quote=True)}" '
        f'alt="{html.escape(work.get("title") or "作品海报", quote=True)}" loading="lazy">'
        for work in posters[:limit]
    )


def render_poster_hero_strip(posters: list[dict[str, Any]]) -> None:
    if posters:
        st.markdown(f'<div class="yg-poster-strip">{_poster_images(posters, "yg-poster", 12)}</div>', unsafe_allow_html=True)


def render_poster_wall(posters: list[dict[str, Any]]) -> None:
    if posters:
        st.markdown(f'<div class="yg-poster-wall">{_poster_images(posters, "yg-poster", 24)}</div>', unsafe_allow_html=True)


def render_side_poster_rail(posters: list[dict[str, Any]]) -> None:
    if posters:
        st.markdown(f'<aside class="yg-poster-rail">{_poster_images(posters, "yg-poster", 10)}</aside>', unsafe_allow_html=True)


def render_blur_poster_background(posters: list[dict[str, Any]]) -> None:
    if posters:
        source = html.escape(posters[0]["_poster_src"], quote=True)
        st.markdown(f'<div class="yg-blurred-banner" style="background-image:url(\'{source}\')"></div>', unsafe_allow_html=True)


def render_page_background(page: dict[str, Any], posters: list[dict[str, Any]]) -> None:
    display_mode = BACKGROUND_MODE if BACKGROUND_MODE in BACKGROUND_DISPLAY_MODES else "soft"
    if display_mode == "off" or not page.get("background_enabled") or page.get("background_mode") == "none":
        return
    mode = page.get("background_mode", "none")
    source = ""
    sources: list[str] = []
    if mode == "custom_image":
        source = _image_src(page.get("background_path"))
    elif mode == "custom_url":
        source = _image_src(page.get("background_url"))
    elif posters:
        selected = posters
        if mode == "auto_recent_finished_poster":
            selected = sorted(posters, key=lambda w: w.get("finish_date") or "", reverse=True)
        elif mode == "auto_top_rated_poster":
            selected = sorted(posters, key=lambda w: float(w.get("score_total") or 0), reverse=True)
        sources.extend(work["_poster_src"] for work in selected[:3])
        source = sources[0]
    if mode.startswith("auto_"):
        local_sources = _local_gallery_sources()
        if local_sources:
            sources = local_sources
            source = ""
        elif not source and Path(LOCAL_GALLERY_FALLBACK).exists():
            source = _image_src(LOCAL_GALLERY_FALLBACK)
    if source and source not in sources:
        sources.insert(0, source)

    overlay = max(0.0, min(float(page.get("overlay_opacity", .78)), .96))
    blur = max(0, min(int(page.get("blur", 2)), 20))
    brightness = max(.4, min(float(page.get("brightness", .95)), 1.2))
    fixed = "fixed" if page.get("fixed", True) else "absolute"
    if sources:
        _background_carousel(sources, overlay, blur, brightness, fixed, display_mode)


def render_glass_card(content: str) -> None:
    st.markdown(f'<div class="yg-glass-card">{content}</div>', unsafe_allow_html=True)


def render_animated_list_item(content: str) -> None:
    st.markdown(f'<div class="yg-animated-item">{content}</div>', unsafe_allow_html=True)


def render_page_shell(page_key: str, title: str, subtitle: str, settings: dict[str, Any], works: list[dict[str, Any]]) -> None:
    st.markdown(
        f'<header class="yg-page-header"><div><small>YANG-GUMI / {html.escape(page_key.upper())}</small>'
        f'<h1>{html.escape(title)}</h1><p>{html.escape(subtitle)}</p></div></header>',
        unsafe_allow_html=True,
    )


def inject_css(settings: dict[str, Any] | None = None) -> None:
    settings = settings or {"global": {}}
    global_settings = settings.get("global", {})
    motion = bool(global_settings.get("enable_motion", True))
    hover = bool(global_settings.get("enable_hover_animation", True)) and motion
    strength = global_settings.get("animation_strength", "light")
    duration = {"off": "0ms", "light": "180ms", "standard": "260ms"}.get(strength, "180ms") if motion else "0ms"
    glass = max(.65, min(float(global_settings.get("content_glass_opacity", .86)), .98))
    poster_opacity = max(.08, min(float(global_settings.get("poster_opacity", .18)), .6))
    hover_css = """
      div[data-testid="stVerticalBlockBorderWrapper"]:hover {transform:translateY(-3px); border-color:rgba(255,92,139,.55); box-shadow:0 18px 42px rgba(0,0,0,.34),0 0 28px rgba(255,92,139,.08);}
      .stButton button:hover {transform:translateY(-1px); border-color:#ff6f9c; box-shadow:0 8px 24px rgba(255,70,128,.18);}
      .yg-poster:hover {transform:translateY(-5px) scale(1.03); opacity:1; box-shadow:0 16px 36px rgba(0,0,0,.45),0 0 0 1px rgba(255,99,151,.45);}
    """ if hover else ""
    disabled_css = "" if motion else "*, *::before, *::after {animation:none!important; transition:none!important; scroll-behavior:auto!important;}"
    st.markdown(f"""
    <style>
      :root {{--yg-pink:#ff5c8a;--yg-pink-2:#ff86ae;--yg-cyan:#55d9ff;--yg-violet:#8f7cff;--yg-bg:#080a12;--yg-panel:#121622;--yg-line:rgba(153,169,205,.18);}}
      html {{scroll-behavior:smooth;color-scheme:dark!important;}}
      body, [data-testid="stAppViewContainer"] {{background:radial-gradient(circle at 82% 0%,rgba(113,78,191,.16),transparent 32%),radial-gradient(circle at 22% 8%,rgba(255,67,126,.12),transparent 30%),#080a12;color:#f4f6fb;}}
      [data-testid="stHeader"] {{background:rgba(8,10,18,.58); backdrop-filter:blur(18px); border-bottom:1px solid rgba(255,255,255,.05);}}
      .block-container {{max-width:1320px; padding-top:2.1rem; padding-bottom:5rem; position:relative; z-index:2;}}
      [data-testid="stSidebar"] {{border-right:1px solid rgba(255,92,139,.18); background:linear-gradient(180deg,rgba(12,15,26,.96),rgba(8,10,18,.98)); backdrop-filter:blur(18px);}}
      [data-testid="stSidebar"] [data-testid="stMarkdownContainer"] p {{color:#9da7bb;}}
      [data-testid="stSidebar"] label[data-baseweb="radio"] {{width:100%;padding:.52rem .65rem;border:1px solid transparent;border-radius:12px;transition:background {duration} ease,color {duration} ease,border-color {duration} ease;}}
      [data-testid="stSidebar"] label[data-baseweb="radio"]:has(input:checked) {{background:linear-gradient(90deg,rgba(255,72,130,.24),rgba(112,92,255,.09));border-color:rgba(255,92,139,.22);color:#fff;box-shadow:inset 3px 0 #ff5c8a;}}
      [data-testid="stSidebar"] .stButton button {{justify-content:flex-start;min-height:2.55rem;padding-left:.8rem;border-color:transparent;background:transparent;box-shadow:none;color:#98a3b7;}}
      [data-testid="stSidebar"] .stButton button:hover {{background:rgba(255,255,255,.04);border-color:rgba(143,157,190,.14);color:#f4f6fb;}}
      [data-testid="stSidebar"] .stButton button[kind="primary"] {{background:linear-gradient(90deg,rgba(255,72,130,.28),rgba(112,92,255,.12));border:1px solid rgba(255,92,139,.28);box-shadow:inset 3px 0 #ff5c8a;color:#fff;}}
      .yg-brand {{display:flex;align-items:center;gap:.8rem;margin:.35rem 0 1.5rem;}}
      .yg-brand-mark {{position:relative;display:grid;place-items:center;width:48px;height:48px;border-radius:15px;background:linear-gradient(145deg,#ff5c8a,#8b68ff);box-shadow:0 12px 28px rgba(255,64,126,.28);color:white;}}
      .yg-brand-mark span {{font-size:1.55rem;font-weight:950;font-style:italic;}}
      .yg-brand-mark i {{position:absolute;right:-5px;top:-7px;width:18px;height:18px;border-radius:50%;display:grid;place-items:center;background:#5cdefd;color:#07101a;font-size:.68rem;font-style:normal;}}
      .yg-brand>div:last-child {{display:flex;flex-direction:column;}}
      .yg-brand strong {{font-size:1.35rem;line-height:1.1;color:#fff;letter-spacing:-.03em;}}
      .yg-brand>div:last-child span {{margin-top:.28rem;color:#8692a8;font-size:.72rem;}}
      .yg-nav-label {{margin:0 0 .55rem .2rem;color:#ff7fa8;font-size:.64rem;font-weight:900;letter-spacing:.16em;}}
      .yg-sidebar-foot {{margin-top:1.6rem;padding:1rem;border:1px solid rgba(132,151,188,.16);border-radius:14px;background:rgba(17,21,34,.68);display:grid;grid-template-columns:auto 1fr;align-items:center;gap:.25rem .5rem;}}
      .yg-sidebar-foot b {{font-size:.68rem;letter-spacing:.13em;color:#dce5f5;}}
      .yg-sidebar-foot small {{grid-column:1/-1;color:#738096;font-size:.68rem;}}
      .yg-status-dot {{width:8px;height:8px;border-radius:50%;background:#58e5a3;box-shadow:0 0 12px rgba(88,229,163,.75);}}
      [data-testid="stMetric"] {{position:relative;overflow:hidden;background:linear-gradient(145deg,rgba(24,29,45,{glass}),rgba(14,18,30,{glass})); border:1px solid var(--yg-line); border-radius:17px; padding:14px 16px; backdrop-filter:blur(14px); box-shadow:0 12px 30px rgba(0,0,0,.18);}}
      [data-testid="stMetric"]::before {{content:"";position:absolute;left:0;top:0;width:100%;height:2px;background:linear-gradient(90deg,var(--yg-pink),var(--yg-violet),var(--yg-cyan));opacity:.8;}}
      [data-testid="stMetricValue"] {{color:#fff;font-weight:800;}}
      div[data-testid="stVerticalBlockBorderWrapper"] {{border:1px solid var(--yg-line); border-radius:18px; background:linear-gradient(150deg,rgba(22,27,42,{glass}),rgba(12,15,25,{glass})); backdrop-filter:blur(15px); box-shadow:0 12px 34px rgba(0,0,0,.22); transition:transform {duration} ease,border-color {duration} ease,box-shadow {duration} ease;}}
      h1 {{font-size:clamp(2.3rem,5vw,4.4rem)!important;line-height:1.02!important;margin:.35rem 0 .55rem!important;background:linear-gradient(105deg,#fff 12%,#ff9fbe 52%,#7de6ff 92%);-webkit-background-clip:text;background-clip:text;color:transparent!important;text-shadow:0 12px 50px rgba(255,69,131,.12);}}
      h2,h3,h4 {{letter-spacing:-.025em;color:#f5f7fb;}}
      h2 {{margin-top:2rem!important;}}
      .yg-kicker {{display:inline-flex;align-items:center;gap:.45rem;padding:.36rem .72rem;border:1px solid rgba(255,92,139,.28);border-radius:999px;background:rgba(255,73,130,.09);color:#ff8aae;font-weight:800;letter-spacing:.11em;text-transform:uppercase;font-size:.72rem;}}
      .yg-kicker::before {{content:"✦";color:var(--yg-cyan);}}
      .yg-muted,[data-testid="stCaptionContainer"] {{color:#98a2b5!important;}}
      .yg-home-hero {{position:relative;display:grid;grid-template-columns:minmax(0,1.25fr) minmax(280px,.75fr);gap:2rem;align-items:center;min-height:310px;margin:1.2rem 0 1.55rem;padding:clamp(1.4rem,4vw,2.8rem);overflow:hidden;border:1px solid rgba(148,162,196,.16);border-radius:26px;background:linear-gradient(125deg,rgba(17,21,35,.97),rgba(11,15,27,.94) 58%,rgba(24,16,38,.95));box-shadow:0 22px 58px rgba(0,0,0,.28);backdrop-filter:blur(20px);}}
      .yg-home-hero::before {{content:"";position:absolute;inset:0;background:radial-gradient(circle at 82% 25%,rgba(92,222,253,.08),transparent 25%),radial-gradient(circle at 14% 95%,rgba(255,72,130,.08),transparent 34%);pointer-events:none;}}
      .yg-home-copy,.yg-vault-orbit {{position:relative;z-index:1;}}
      .yg-hero-eyebrow {{color:#63dcff;font-size:.68rem;font-weight:900;letter-spacing:.17em;}}
      .yg-home-copy h2 {{max-width:650px;margin:.6rem 0 .85rem!important;font-size:clamp(1.9rem,3.3vw,3.4rem);line-height:1.08;color:#fff;}}
      .yg-home-copy p {{max-width:610px;margin:0;color:#aab4c7;font-size:1rem;line-height:1.8;}}
      .yg-hero-chips {{display:flex;flex-wrap:wrap;gap:.55rem;margin-top:1.35rem;}}
      .yg-hero-chips span {{padding:.42rem .7rem;border:1px solid rgba(149,163,198,.18);border-radius:999px;background:rgba(9,13,23,.55);color:#ced6e6;font-size:.72rem;}}
      .yg-vault-orbit {{min-height:220px;display:grid;place-items:center;}}
      .yg-orbit-ring {{position:absolute;width:205px;height:205px;border:1px solid rgba(105,220,255,.3);border-radius:50%;box-shadow:0 0 55px rgba(105,220,255,.08),inset 0 0 40px rgba(255,75,135,.07);}}
      .yg-orbit-ring::before,.yg-orbit-ring::after {{content:"";position:absolute;inset:17px;border:1px dashed rgba(255,105,157,.26);border-radius:50%;}}
      .yg-orbit-ring::after {{inset:52px;border-style:solid;border-color:rgba(125,104,255,.26);}}
      .yg-vault-number {{position:relative;display:flex;flex-direction:column;align-items:center;}}
      .yg-vault-number small {{color:#7ce5ff;font-size:.56rem;font-weight:900;letter-spacing:.18em;}}
      .yg-vault-number strong {{font-size:4.3rem;line-height:1;color:#fff;text-shadow:0 0 30px rgba(255,92,138,.25);}}
      .yg-vault-number span {{color:#8794aa;font-size:.68rem;}}
      .yg-orbit-note {{position:absolute;display:flex;flex-direction:column;min-width:78px;padding:.55rem .7rem;border:1px solid rgba(150,165,200,.2);border-radius:12px;background:rgba(8,11,20,.84);box-shadow:0 10px 26px rgba(0,0,0,.28);}}
      .yg-orbit-note b {{color:#fff;font-size:.9rem;}}
      .yg-orbit-note span {{color:#75839a;font-size:.58rem;}}
      .yg-orbit-note-a {{right:0;top:28px;}} .yg-orbit-note-b {{left:-4px;bottom:18px;}}
      .yg-season-title {{display:flex;align-items:end;justify-content:space-between;margin:2.1rem 0 .85rem;}}
      .yg-season-title small {{color:#63dcff;font-size:.6rem;font-weight:900;letter-spacing:.16em;}}
      .yg-season-title h3 {{margin:.16rem 0 0!important;font-size:1.45rem;}}
      .yg-season-title>span {{color:#8793a8;font-size:.7rem;}}
      .yg-season-grid {{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:.8rem;margin-bottom:1.6rem;}}
      .yg-season-window {{min-width:0;overflow:hidden;border:1px solid rgba(145,161,196,.18);border-radius:17px;background:rgba(11,15,26,.82);box-shadow:0 14px 34px rgba(0,0,0,.24);backdrop-filter:blur(15px);}}
      .yg-season-window>header {{display:flex;align-items:center;justify-content:space-between;padding:.75rem .85rem;border-bottom:1px solid rgba(145,161,196,.12);}}
      .yg-season-window>header div {{display:flex;flex-direction:column;}}
      .yg-season-window>header small {{color:#ff7da7;font-size:.56rem;font-weight:900;letter-spacing:.12em;}}
      .yg-season-window>header b {{color:#edf2fa;font-size:.82rem;}}
      .yg-season-window>header>span {{display:grid;place-items:center;width:29px;height:29px;border-radius:50%;background:rgba(101,220,255,.1);color:#75e3ff;font-size:.64rem;font-weight:900;}}
      .yg-season-screen {{height:218px;overflow:hidden;background:#020307;}}
      .yg-season-track {{display:flex;width:max-content;height:100%;gap:.55rem;padding:.65rem;animation:yg-season-scroll 28s linear infinite;}}
      .yg-season-window:hover .yg-season-track {{animation-play-state:paused;}}
      .yg-season-card {{width:112px;flex:0 0 112px;display:flex;flex-direction:column;gap:.4rem;}}
      .yg-season-card img {{width:112px;height:164px;object-fit:cover;border-radius:10px;box-shadow:0 8px 22px rgba(0,0,0,.45);}}
      .yg-season-card strong {{overflow:hidden;color:#dfe5f0;font-size:.67rem;line-height:1.35;white-space:nowrap;text-overflow:ellipsis;}}
      .yg-season-empty {{height:100%;display:flex;flex-direction:column;align-items:center;justify-content:center;padding:.7rem;color:#8b96aa;text-align:center;}}
      .yg-season-black {{width:88%;height:118px;display:grid;place-items:center;margin-bottom:.7rem;border:1px solid #161a22;border-radius:11px;background:linear-gradient(145deg,#000,#080a0f);box-shadow:inset 0 0 30px #000;}}
      .yg-season-black span {{color:#292e38;font-size:.6rem;font-weight:900;letter-spacing:.22em;}}
      .yg-season-empty strong {{color:#a8b0bf;font-size:.72rem;}}
      .yg-season-empty small {{margin-top:.2rem;color:#ff7da7;font-size:.66rem;}}
      .yg-empty-state {{display:flex;align-items:center;gap:1rem;margin:1rem 0;padding:1.3rem 1.45rem;border:1px dashed rgba(135,155,195,.28);border-radius:18px;background:linear-gradient(120deg,rgba(16,21,34,.82),rgba(21,16,34,.68));}}
      .yg-empty-sigil {{display:grid;place-items:center;flex:0 0 48px;height:48px;border-radius:16px;background:linear-gradient(145deg,rgba(255,86,139,.25),rgba(98,213,255,.16));color:#ff82aa;font-size:1.35rem;box-shadow:inset 0 0 0 1px rgba(255,255,255,.05);}}
      .yg-empty-state strong {{display:block;color:#f5f7fb;font-size:1rem;}}
      .yg-empty-state p {{margin:.25rem 0 0!important;color:#8f9aaf!important;font-size:.8rem!important;}}
      .yg-section-heading {{display:flex;align-items:end;justify-content:space-between;margin:2.2rem 0 .85rem;padding-bottom:.65rem;border-bottom:1px solid rgba(145,158,190,.13);}}
      .yg-section-heading small {{color:#ff79a4;font-size:.6rem;font-weight:900;letter-spacing:.16em;}}
      .yg-section-heading h3 {{margin:.12rem 0 0!important;font-size:1.45rem;}}
      .yg-section-heading>span {{color:#75839a;font-size:.7rem;}}
      .yg-work-title {{display:flex;align-items:center;gap:.7rem;margin-bottom:.28rem;}}
      .yg-work-title strong {{color:#f7f8fc;font-size:1.08rem;line-height:1.35;}}
      .yg-rank-number {{display:inline-flex;padding:.27rem .45rem;border-radius:8px;background:linear-gradient(120deg,rgba(255,81,135,.22),rgba(126,103,255,.18));color:#ff87ad;font-size:.68rem;font-weight:900;letter-spacing:.04em;}}
      .yg-score-row {{display:flex;flex-wrap:wrap;gap:.45rem;margin:.48rem 0 .52rem;}}
      .yg-score-row>span {{display:flex;align-items:baseline;gap:.35rem;padding:.3rem .55rem;border:1px solid rgba(142,158,192,.16);border-radius:9px;background:rgba(8,11,20,.58);}}
      .yg-score-row small {{color:#77859d;font-size:.53rem;font-weight:900;letter-spacing:.12em;}}
      .yg-score-row b {{color:#eef2fa;font-size:.82rem;}}
      .yg-score-row .positive b {{color:#67e4ad;}} .yg-score-row .negative b {{color:#ff7894;}} .yg-score-row .neutral b {{color:#6edcff;}}
      .yg-meta-pills {{display:flex;flex-wrap:wrap;gap:.35rem;margin-top:.3rem;}}
      .yg-meta-pills span {{padding:.25rem .5rem;border-radius:999px;background:rgba(91,111,159,.12);color:#9daac0;font-size:.62rem;}}
      .yg-review-line {{margin-top:.6rem;padding-left:.7rem;border-left:2px solid #ff5c8a;color:#aab5c9;font-size:.78rem;font-style:italic;}}
      div[data-testid="stVerticalBlockBorderWrapper"] [data-testid="stImage"] img {{border-radius:13px;box-shadow:0 10px 26px rgba(0,0,0,.34);}}
      .stButton button {{min-height:2.55rem;border-radius:12px;border-color:rgba(147,159,188,.22);background:rgba(20,24,38,.82);color:#eef1f8;transition:transform {duration} ease,border-color {duration} ease,background {duration} ease,box-shadow {duration} ease;}}
      .stButton button[kind="primary"] {{border:0;background:linear-gradient(100deg,#ff4f83,#d84dbe 55%,#7b6dff);color:white;font-weight:800;box-shadow:0 10px 28px rgba(255,67,125,.24);}}
      [data-baseweb="input"],[data-baseweb="select"]>div,[data-baseweb="textarea"] {{background:rgba(11,14,24,.88)!important;border-color:rgba(143,157,190,.24)!important;border-radius:12px!important;}}
      [data-baseweb="tag"] {{background:rgba(255,78,134,.16)!important;border:1px solid rgba(255,101,151,.24);}}
      [data-testid="stExpander"] {{border:1px solid var(--yg-line)!important;border-radius:16px!important;background:rgba(14,18,30,.72)!important;overflow:hidden;}}
      [data-testid="stDataFrame"] {{border:1px solid var(--yg-line);border-radius:16px;overflow:hidden;box-shadow:0 14px 34px rgba(0,0,0,.22);}}
      [data-testid="stAlert"] {{border-radius:15px;border-color:rgba(82,192,255,.24);background:rgba(32,79,126,.18);}}
      hr {{border-color:rgba(151,164,197,.14)!important;}}
      .yg-page-bg, .yg-page-overlay {{inset:0; pointer-events:none;}}
      .yg-page-bg {{z-index:-2;}}
      .yg-page-overlay {{z-index:-1;}}
      .yg-bg-single {{background-repeat:no-repeat;background-color:#080a12;}}
      [data-testid="stMainBlockContainer"] {{position:relative;z-index:2;}}
      .yg-background-carousel {{inset:0;z-index:-2;pointer-events:none;overflow:hidden;background:radial-gradient(circle at 70% 22%,rgba(21,21,38,.46) 0,rgba(10,13,23,.44) 48%,rgba(8,10,18,.48) 100%);}}
      .yg-background-root {{position:relative;height:0;z-index:0;}}
      .yg-bg-radio {{position:fixed;left:-9999px;opacity:0;}}
      .yg-bg-slides,.yg-bg-slide {{position:absolute;inset:0;}}
      .yg-bg-slides {{z-index:0;}}
      .yg-bg-slide {{opacity:0;background-repeat:no-repeat;background-color:#080a12;animation-name:yg-bg-cycle;animation-timing-function:ease-in-out;animation-iteration-count:infinite;transition:opacity 2.4s ease;will-change:opacity;}}
      .yg-bg-mode-soft {{transform:scale(1.01);}}
      .yg-bg-mode-contain {{transform:none;}}
      .yg-bg-mode-corner {{transform:none;}}
      .yg-background-carousel>.yg-page-overlay {{position:absolute;inset:0;z-index:1;}}
      .yg-bg-controls {{position:absolute;right:1rem;top:.35rem;z-index:999;display:flex;align-items:center;gap:.5rem;pointer-events:auto;padding:.44rem .52rem;border:1px solid rgba(155,169,202,.2);border-radius:999px;background:rgba(8,11,20,.72);backdrop-filter:blur(14px);box-shadow:0 12px 30px rgba(0,0,0,.3);}}
      .yg-bg-control-group {{display:none;gap:.32rem;}}
      .yg-bg-control-auto {{display:flex;}}
      .yg-bg-control-group label,.yg-bg-play {{display:grid;place-items:center;width:28px;height:28px;margin:0;border:1px solid rgba(160,176,211,.18);border-radius:50%;background:rgba(30,36,54,.76);color:#f6f8ff;font-size:1.15rem;line-height:1;cursor:pointer;transition:transform .18s ease,background .18s ease;}}
      .yg-bg-control-group label:hover,.yg-bg-play:hover {{transform:scale(1.08);background:rgba(255,78,134,.34);}}
      .yg-bg-dots {{display:flex;gap:.28rem;padding:0 .15rem;}}
      .yg-bg-dots label {{width:7px;height:7px;border-radius:50%;background:rgba(221,227,240,.34);cursor:pointer;transition:width .18s ease,background .18s ease;}}
      #yg-bg-auto:not(:checked) ~ .yg-bg-controls .yg-bg-control-auto {{display:none;}}
      #yg-bg-auto:checked ~ .yg-bg-controls .yg-bg-play {{color:#64e1ff;border-color:rgba(100,225,255,.45);}}
      #yg-bg-1:checked ~ .yg-bg-controls .yg-bg-dots label[for="yg-bg-1"],#yg-bg-2:checked ~ .yg-bg-controls .yg-bg-dots label[for="yg-bg-2"],#yg-bg-3:checked ~ .yg-bg-controls .yg-bg-dots label[for="yg-bg-3"],#yg-bg-4:checked ~ .yg-bg-controls .yg-bg-dots label[for="yg-bg-4"],#yg-bg-5:checked ~ .yg-bg-controls .yg-bg-dots label[for="yg-bg-5"],#yg-bg-6:checked ~ .yg-bg-controls .yg-bg-dots label[for="yg-bg-6"] {{width:16px;background:#ff5c8a;}}
      .yg-bg-grid {{display:grid; grid-template-columns:repeat(8,1fr); gap:10px; padding:12px; overflow:hidden; opacity:{poster_opacity};}}
      .yg-bg-wall {{grid-template-columns:repeat(6,1fr); gap:14px;}}
      .yg-bg-poster {{width:100%;aspect-ratio:2/3;object-fit:cover;border-radius:10px;box-shadow:0 8px 24px rgba(0,0,0,.34);}}
      .yg-poster-strip {{display:flex;gap:12px;overflow:hidden;margin:.8rem 0 1.5rem;padding:12px;border:1px solid var(--yg-line);border-radius:18px;background:linear-gradient(90deg,rgba(18,22,35,.82),rgba(18,22,35,.42));backdrop-filter:blur(12px);mask-image:linear-gradient(90deg,transparent,#000 2%,#000 98%,transparent);}}
      .yg-poster {{width:70px;height:102px;flex:0 0 auto;object-fit:cover;border-radius:11px;box-shadow:0 8px 22px rgba(0,0,0,.32);opacity:.9;transition:transform {duration} ease,opacity {duration} ease,box-shadow {duration} ease;}}
      .yg-poster-wall {{display:flex;gap:10px;overflow:hidden;margin:.7rem 0 1.4rem;padding:12px;border:1px solid var(--yg-line);border-radius:18px;background:rgba(13,17,28,.68);backdrop-filter:blur(14px);}}
      .yg-poster-wall .yg-poster {{width:58px;height:84px;}}
      .yg-poster-rail {{position:fixed;right:18px;top:110px;z-index:3;display:grid;grid-template-columns:repeat(2,48px);gap:8px;padding:9px;border:1px solid var(--yg-line);border-radius:16px;background:rgba(10,13,22,.7);backdrop-filter:blur(12px);opacity:.78;pointer-events:auto;}}
      .yg-poster-rail .yg-poster {{width:48px;height:70px;}}
      .yg-blurred-banner {{height:118px; border-radius:16px; margin:.5rem 0 1.25rem; background-size:cover; background-position:center; filter:blur(1px) brightness(.9); opacity:.35;}}
      .yg-glass-card {{padding:1rem;border:1px solid var(--yg-line);border-radius:16px;background:rgba(17,21,34,{glass});backdrop-filter:blur(14px);}}
      a {{color:#68dbff!important;}}
      code {{color:#ff8db1;background:rgba(255,82,137,.09);}}
      .yg-animated-item {{animation:none;}}
      @keyframes yg-page-in {{from {{opacity:0; transform:translateY(4px)}} to {{opacity:1; transform:none}}}}
      @keyframes yg-card-in {{from {{opacity:.2; transform:translateY(7px)}} to {{opacity:1; transform:none}}}}
      @keyframes yg-season-scroll {{from {{transform:translateX(0)}} to {{transform:translateX(calc(-50% - .275rem))}}}}
      {hover_css}
      {disabled_css}

      /* 第八步复刻规范：Anibt 顶栏/列表 + Bangumi 档案统计 + 番库追番卡片。 */
      :root {{--yg-pink:#d65a7a;--yg-pink-2:#ed7896;--yg-cyan:#69aeb5;--yg-violet:#7c748f;--yg-bg:#202020;--yg-panel:#292929;--yg-panel-2:#303030;--yg-line:rgba(255,255,255,.085);}}
      body,[data-testid="stAppViewContainer"] {{background:#202020;color:#e7e7e9;font-family:Inter,"Segoe UI","Microsoft YaHei",sans-serif;}}
      [data-testid="stHeader"] {{display:none;height:0;background:transparent;border:0;}}
      html {{scroll-behavior:smooth;}}
      html,body,[data-testid="stAppViewContainer"] {{overflow-x:hidden;}}
      .block-container {{width:100%;max-width:1440px;min-width:0;padding:4.15rem 1.5rem 4rem;box-sizing:border-box;animation:none;}}
      [data-testid="stSidebar"] {{display:none;}}
      .st-key-top_navigation {{position:fixed;top:0;left:0;right:0;z-index:999;background:rgba(19,20,22,.94);border-bottom:1px solid var(--yg-line);backdrop-filter:blur(18px);}}
      .st-key-top_navigation>div {{max-width:1440px;margin:auto;padding:.65rem 1.5rem;}}
      .st-key-top_navigation [data-testid="stHorizontalBlock"] {{gap:.35rem;align-items:center;}}
      .yg-top-brand {{display:flex;flex-direction:column;line-height:1.05;padding:.25rem 0;white-space:nowrap;}}
      .yg-top-brand b {{font-size:1.15rem;letter-spacing:.06em;color:#f0f0f2;}}
      .yg-top-brand b span {{color:var(--yg-pink);}}
      .yg-top-brand small {{margin-top:.28rem;color:#77797f;font-size:.52rem;letter-spacing:.14em;}}
      .st-key-top_navigation .stButton button {{min-height:2.35rem;padding:.35rem .55rem;border:0;border-radius:10px;background:transparent;box-shadow:none;color:#aaaab0;font-size:.78rem;}}
      .st-key-top_navigation .stButton button:hover {{transform:none;background:#292a2d;border:0;box-shadow:none;color:#fff;}}
      .st-key-top_navigation .stButton button[kind="primary"] {{background:#292a2d;color:#fff;box-shadow:inset 0 -2px var(--yg-pink);}}
      .yg-page-header {{display:flex;align-items:end;justify-content:space-between;margin:.35rem 0 .85rem;padding-bottom:.65rem;border-bottom:1px solid var(--yg-line);}}
      .yg-page-header small {{color:var(--yg-pink);font-size:.58rem;font-weight:700;letter-spacing:.13em;}}
      .yg-page-header h1 {{margin:.18rem 0 .12rem!important;background:none!important;color:#ededee!important;text-shadow:none!important;font-size:clamp(1.85rem,3vw,2.55rem)!important;line-height:1.08!important;}}
      .yg-page-header p {{margin:0;color:#8f9096;font-size:.78rem;}}
      h2 {{margin-top:1.3rem!important;font-size:1.35rem!important;}} h3 {{font-size:1.05rem!important;}}
      div[data-testid="stVerticalBlockBorderWrapper"] {{border:1px solid var(--yg-line);border-radius:13px;background:rgba(27,28,31,.96);backdrop-filter:none;box-shadow:none;animation:yg-soft-enter .28s cubic-bezier(.22,.8,.3,1) both;transition:transform .28s cubic-bezier(.22,.8,.3,1),border-color .28s ease,background .28s ease;}}
      div[data-testid="stVerticalBlockBorderWrapper"]:hover {{transform:translateY(-2px);border-color:rgba(214,90,122,.38);box-shadow:none;}}
      [data-testid="stMetric"] {{padding:.75rem .9rem;border:1px solid var(--yg-line);border-radius:11px;background:#1b1c1f;backdrop-filter:none;box-shadow:none;}}
      [data-testid="stMetric"]::before {{display:none;}}
      [data-testid="stMetricValue"] {{font-size:1.45rem;}}
      .stButton button {{min-height:2.35rem;border:1px solid var(--yg-line);border-radius:10px;background:#202125;box-shadow:none;color:#dddde0;}}
      .stButton button:hover {{transform:none;border-color:rgba(214,90,122,.55);background:#28292d;box-shadow:none;}}
      .stButton button[kind="primary"] {{border:1px solid rgba(214,90,122,.48);background:#762b41;box-shadow:none;}}
      [data-baseweb="input"],[data-baseweb="select"]>div,[data-baseweb="textarea"] {{background:#111214!important;border-color:var(--yg-line)!important;border-radius:9px!important;}}
      [data-baseweb="base-input"],[data-baseweb="input"]>div,[data-testid="stNumberInput"]>div,[data-testid="stDateInput"]>div {{background:#111214!important;color:#e7e7e9!important;}}
      [data-baseweb="input"] input,[data-baseweb="base-input"] input,[data-baseweb="textarea"] textarea,[data-testid="stNumberInput"] input,[data-testid="stDateInput"] input,input:not([type="checkbox"]):not([type="radio"]),textarea,select {{background-color:#111214!important;color:#e7e7e9!important;-webkit-text-fill-color:#e7e7e9!important;caret-color:#e7e7e9!important;color-scheme:dark!important;}}
      [data-baseweb="select"] input,[data-baseweb="select"] span,[data-baseweb="select"] svg,[data-testid="stNumberInput"] svg,[data-testid="stDateInput"] svg {{color:#e7e7e9!important;fill:#e7e7e9!important;-webkit-text-fill-color:#e7e7e9!important;}}
      [data-testid="stNumberInput"] button,[data-testid="stDateInput"] button {{background:#17181b!important;color:#e7e7e9!important;border-color:var(--yg-line)!important;}}
      [data-baseweb="button-group"] button,[data-testid="stBaseButton-segmented_control"] {{background:#0e1117!important;color:#e7e7e9!important;border-color:rgba(250,250,250,.20)!important;-webkit-text-fill-color:#e7e7e9!important;color-scheme:dark!important;}}
      [data-baseweb="button-group"] button:hover,[data-testid="stBaseButton-segmented_control"]:hover {{background:#1b1d22!important;color:#fff!important;}}
      [data-baseweb="button-group"] button[kind="segmented_controlActive"],[data-testid="stBaseButton-segmented_controlActive"] {{background:rgba(214,90,122,.16)!important;color:#ed7896!important;border-color:#d65a7a!important;-webkit-text-fill-color:#ed7896!important;}}
      [data-testid="stFileUploaderDropzone"],div[role="listbox"],ul[role="listbox"] {{background:#151619!important;color:#e7e7e9!important;border-color:var(--yg-line)!important;color-scheme:dark!important;}}
      input:-webkit-autofill,input:-webkit-autofill:hover,input:-webkit-autofill:focus {{-webkit-box-shadow:0 0 0 1000px #111214 inset!important;-webkit-text-fill-color:#e7e7e9!important;}}
      input:disabled,textarea:disabled,[aria-disabled="true"] {{opacity:.72!important;background:#17181b!important;color:#a8a9ae!important;-webkit-text-fill-color:#a8a9ae!important;}}
      [data-testid="stExpander"] {{border:1px solid var(--yg-line)!important;border-radius:11px!important;background:#151619!important;}}
      [data-testid="stExpander"] details,[data-testid="stExpander"] summary {{background:#151619!important;color:#e7e7e9!important;color-scheme:dark!important;}}
      [data-testid="stAlert"] {{border-radius:10px;background:#161a20;}}
      .yg-section-heading {{margin:1.35rem 0 .65rem;padding-bottom:.55rem;border-bottom:1px solid var(--yg-line);}}
      .yg-section-heading small {{color:var(--yg-pink);font-size:.52rem;}}
      .yg-section-heading h3 {{font-size:1.12rem!important;}}
      .yg-work-title strong {{font-size:.95rem;}}
      .yg-rank-number {{background:#302027;color:#e7809b;border-radius:6px;}}
      .yg-score-row {{margin:.32rem 0;gap:.28rem;}}
      .yg-score-row>span {{padding:.2rem .4rem;border-radius:6px;background:#121315;}}
      .yg-meta-pills span {{padding:.17rem .38rem;background:#26272a;color:#aaaab0;font-size:.58rem;}}
      .yg-review-line {{margin-top:.4rem;font-size:.7rem;}}
      div[data-testid="stVerticalBlockBorderWrapper"] [data-testid="stImage"] img {{border-radius:9px;box-shadow:none;}}
      .yg-profile {{display:flex;align-items:center;gap:1rem;margin-bottom:1rem;padding:1rem 1.1rem;border:1px solid var(--yg-line);border-radius:13px;background:rgba(28,29,32,.94);}}
      .yg-profile-avatar {{display:grid;place-items:center;width:64px;height:64px;flex:0 0 64px;border-radius:50%;background:#44232d;color:#ee8aa3;font-size:1.65rem;font-weight:800;}}
      .yg-profile-copy {{display:flex;min-width:170px;flex-direction:column;}}
      .yg-profile-copy>b {{font-size:1.15rem;}} .yg-profile-copy>span {{color:#aaaab0;font-size:.72rem;}} .yg-profile-copy>small {{margin-top:.35rem;color:#686a70;font-size:.5rem;letter-spacing:.1em;}}
      .yg-profile-stats {{display:grid;grid-template-columns:repeat(6,minmax(76px,1fr));flex:1;overflow:hidden;border:1px solid var(--yg-line);border-radius:10px;}}
      .yg-profile-stats>div {{display:flex;flex-direction:column;padding:.55rem .7rem;border-right:1px solid var(--yg-line);background:#212225;}}
      .yg-profile-stats small {{color:#85868c;font-size:.58rem;}} .yg-profile-stats b {{color:#eee;font-size:1rem;}}
      .yg-category-overview {{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:.65rem;margin-bottom:1.1rem;}}
      .yg-category-overview article {{overflow:hidden;border:1px solid var(--yg-line);border-radius:12px;background:#1a1b1e;}}
      .yg-category-overview header,.yg-category-overview footer {{display:flex;align-items:center;justify-content:space-between;padding:.65rem .75rem;}}
      .yg-category-overview header {{border-bottom:1px solid var(--yg-line);}} .yg-category-overview header span {{color:var(--yg-pink);font-weight:800;}}
      .yg-category-counts {{display:grid;grid-template-columns:repeat(2,1fr);gap:.35rem;padding:.6rem .75rem;color:#888a90;font-size:.62rem;}}
      .yg-category-counts b {{color:#d6d6d9;}} .yg-category-overview footer {{border-top:1px solid var(--yg-line);color:#bbb;font-size:.62rem;}}
      .yg-category-overview footer small {{max-width:65%;overflow:hidden;color:#77797f;white-space:nowrap;text-overflow:ellipsis;}}
      .yg-score-distribution {{padding:.85rem;border:1px solid var(--yg-line);border-radius:12px;background:#1a1b1e;}}
      .yg-score-distribution header {{display:flex;justify-content:space-between;color:#ddd;}} .yg-score-distribution header span {{color:#74767c;font-size:.55rem;}}
      .yg-score-distribution>div {{display:flex;align-items:end;gap:.45rem;height:118px;margin-top:.7rem;}}
      .yg-score-distribution>div>div {{display:flex;height:100%;flex:1;flex-direction:column;align-items:center;justify-content:end;gap:.15rem;}}
      .yg-score-distribution i {{width:70%;min-height:4px;border-radius:3px 3px 0 0;background:#96989d;}} .yg-score-distribution b,.yg-score-distribution small {{font-size:.56rem;color:#898a90;}}
      .yg-season-title {{margin:1.25rem 0 .6rem;}} .yg-season-title h3 {{font-size:1.12rem!important;}}
      .yg-season-grid {{gap:.6rem;margin-bottom:1.1rem;}} .yg-season-window {{border-radius:12px;background:#151619;box-shadow:none;backdrop-filter:none;}}
      .yg-season-screen {{height:292px;}} .yg-season-card {{width:128px;flex-basis:128px;}} .yg-season-card img {{width:128px;height:188px;border-radius:10px;box-shadow:0 10px 24px rgba(0,0,0,.28);}}
      .yg-season-memory-stack {{position:relative;height:100%;overflow:hidden;background:#1d1e20;}}
      .yg-season-memory-stack:before,.yg-season-memory-stack:after {{position:absolute;right:0;left:0;z-index:8;height:22px;content:"";pointer-events:none;backdrop-filter:blur(3px)}}
      .yg-season-memory-stack:before {{top:0;background:linear-gradient(#1d1e20,transparent)}} .yg-season-memory-stack:after {{bottom:0;background:linear-gradient(transparent,#1d1e20)}}
      .yg-season-memory {{position:absolute;inset:-12px 0;display:grid;grid-template-columns:115px 1fr;gap:.8rem;padding:1rem .85rem;background:#1d1e20;opacity:1;will-change:transform,opacity;}}
      .yg-season-memory img {{width:115px;height:178px;align-self:center;object-fit:cover;border-radius:9px;}}
      .yg-season-memory>div {{display:flex;min-width:0;flex-direction:column;justify-content:center;}}
      .yg-season-memory>div>span {{align-self:flex-start;padding:.16rem .4rem;border:1px solid rgba(214,90,122,.35);border-radius:999px;color:#df7791;font-size:.55rem;}}
      .yg-season-memory strong {{margin-top:.45rem;overflow:hidden;color:#eee;font-size:.9rem;white-space:nowrap;text-overflow:ellipsis;}}
      .yg-season-memory small {{overflow:hidden;color:#85868c;font-size:.58rem;white-space:nowrap;text-overflow:ellipsis;}}
      .yg-season-memory p {{display:flex;flex-direction:column;gap:.18rem;margin:.5rem 0 .25rem;color:#ccc;font-size:.7rem;}}
      .yg-season-memory p b {{font-weight:650;}}
      .yg-season-memory em {{display:-webkit-box;overflow:hidden;color:#999;font-size:.6rem;font-style:normal;-webkit-box-orient:vertical;-webkit-line-clamp:2;}}
      .yg-season-memory footer {{margin-top:.55rem;color:#6f7176;font-size:.55rem;}}
      .yg-fanku-carousel {{position:relative;height:350px;margin:.25rem 0 1rem;overflow:hidden;border:1px solid rgba(214,90,122,.22);border-radius:18px;background:radial-gradient(circle at 72% 32%,#32252c 0,#202125 48%,#18191c 100%);}}
      .yg-fanku-scenes {{position:absolute;inset:0 0 30px;}}
      .yg-fanku-scene {{position:absolute;inset:0;display:grid;grid-template-columns:42% 58%;opacity:1;}}
      .yg-fanku-copy {{z-index:4;display:flex;flex-direction:column;justify-content:center;padding:2.15rem 1rem 2rem 2.3rem;}}
      .yg-fanku-copy>small {{color:var(--yg-pink-2);font-size:.62rem;font-weight:800;letter-spacing:.14em;}}
      .yg-fanku-copy h2 {{max-width:540px;margin:.55rem 0 .4rem!important;color:#f2f2f3;font-size:clamp(1.8rem,3.5vw,3rem)!important;line-height:1.12;}}
      .yg-fanku-copy p {{overflow:hidden;margin:0 0 1.2rem;color:#999ba1;font-size:.82rem;white-space:nowrap;text-overflow:ellipsis;}}
      .yg-fanku-copy>div {{display:flex;flex-wrap:wrap;gap:.55rem;}}
      .yg-fanku-copy b,.yg-fanku-copy span {{padding:.42rem .68rem;border:1px solid rgba(214,90,122,.26);border-radius:999px;background:#252327;color:#c9c9cd;font-size:.72rem;}}
      .yg-fanku-copy b {{background:#762b41;color:#fff;}}
      .yg-fanku-stage {{position:relative;min-width:0;perspective:1000px;}}
      .yg-fanku-poster {{position:absolute;top:50%;left:50%;width:158px;height:238px;object-fit:cover;border-radius:15px;box-shadow:0 18px 42px rgba(0,0,0,.44);transition:transform .55s ease,opacity .55s ease,filter .55s ease;}}
      .yg-fanku-poster:hover {{filter:brightness(1.08);}}
      .yg-fanku-pos-c0 {{z-index:5;opacity:1;transform:translate(-50%,-50%) scale(1.14);}}
      .yg-fanku-pos-m1 {{z-index:4;opacity:.74;transform:translate(-138%,-50%) scale(.88) rotateY(7deg);}}
      .yg-fanku-pos-p1 {{z-index:4;opacity:.74;transform:translate(38%,-50%) scale(.88) rotateY(-7deg);}}
      .yg-fanku-pos-m2 {{z-index:3;opacity:.34;transform:translate(-205%,-50%) scale(.68) rotateY(10deg);}}
      .yg-fanku-pos-p2 {{z-index:3;opacity:.34;transform:translate(105%,-50%) scale(.68) rotateY(-10deg);}}
      .yg-fanku-carousel>footer {{position:absolute;right:1.15rem;bottom:.65rem;left:1.15rem;display:flex;align-items:center;justify-content:space-between;color:#7e8086;font-size:.58rem;}}
      .yg-fanku-carousel>footer>div {{display:flex;gap:.28rem;}} .yg-fanku-carousel>footer i {{display:block;width:18px;height:2px;background:#4a4b50;}} .yg-fanku-carousel>footer i.active {{background:var(--yg-pink);}}
      .yg-current-season-empty {{display:flex;align-items:center;justify-content:space-between;min-height:155px;margin:.25rem 0 1rem;padding:1.7rem 2rem;border:1px dashed rgba(214,90,122,.28);border-radius:18px;background:#1a1b1e;}}
      .yg-current-season-empty small {{color:var(--yg-pink);}} .yg-current-season-empty h2 {{margin:.35rem 0!important;font-size:1.55rem!important;}} .yg-current-season-empty p,.yg-current-season-empty>span {{color:#74767c;font-size:.7rem;}}
      [class*="st-key-grid_card_"]>div[data-testid="stVerticalBlockBorderWrapper"] {{min-height:340px;border-color:rgba(126,40,59,.72);background:#1b1b1e;transition:border-color .18s ease,transform .18s ease;}}
      [class*="st-key-grid_card_"]>div[data-testid="stVerticalBlockBorderWrapper"]:hover {{transform:translateY(-2px);border-color:rgba(214,90,122,.65);}}
      [class*="st-key-grid_card_"] h3 {{display:-webkit-box;overflow:hidden;margin:.05rem 0 .25rem!important;font-size:1rem!important;-webkit-box-orient:vertical;-webkit-line-clamp:2;}}
      [class*="st-key-grid_card_"] [data-testid="stImage"] img {{aspect-ratio:2/3;object-fit:cover;}}
      .yg-grid-review {{display:-webkit-box;overflow:hidden;margin-top:.55rem;color:#96979d;font-size:.65rem;-webkit-box-orient:vertical;-webkit-line-clamp:1;}}
      .yg-category-overview header b {{font-size:.92rem;}} .yg-category-overview header span {{font-size:1.05rem;}} .yg-category-counts {{font-size:.7rem;}}
      @keyframes yg-soft-enter {{from {{opacity:.25;transform:translateY(8px)}} to {{opacity:1;transform:translateY(0)}}}}

      /* 第十一步：统一海报层级、可读字号与克制的 ACGN 档案装饰。 */
      :root {{
        --yg-font-base:16px;--yg-font-small:14px;--yg-font-micro:12px;
        --yg-font-nav:15px;--yg-font-card-title:19px;--yg-font-card-subtitle:14px;
        --yg-font-score:17px;--yg-poster-small:96px;--yg-poster-medium:140px;
        --yg-poster-large:160px;--yg-poster-hero:220px;
      }}
      html,body,[data-testid="stAppViewContainer"] {{font-size:var(--yg-font-base);}}
      [data-testid="stAppViewContainer"] {{background-color:#202020;background-image:linear-gradient(rgba(255,255,255,.012) 1px,transparent 1px),linear-gradient(90deg,rgba(255,255,255,.012) 1px,transparent 1px);background-size:28px 28px;}}
      .poster-small {{width:var(--yg-poster-small);aspect-ratio:2/3;object-fit:cover;}}
      .poster-medium {{width:var(--yg-poster-medium);aspect-ratio:2/3;object-fit:cover;}}
      .poster-large {{width:var(--yg-poster-large);aspect-ratio:2/3;object-fit:cover;}}
      .poster-hero {{width:var(--yg-poster-hero);aspect-ratio:2/3;object-fit:cover;}}
      .poster-small,.poster-medium,.poster-large,.poster-hero,.work-list-poster,.ranking-poster,.compare-poster {{border-radius:12px;object-fit:cover;box-shadow:0 10px 24px rgba(0,0,0,.24);transition:transform .28s ease,filter .28s ease;}}
      .anime-chip,.yg-meta-pills span {{font-size:13px!important;line-height:1.25;}}
      .score-badge,.diff-badge,.yg-score-row b {{font-size:15px!important;}}
      .decor-label {{display:block;margin-bottom:.28rem;color:var(--yg-pink-2)!important;font-size:12px!important;font-weight:800;letter-spacing:.14em;}}
      .archive-card {{position:relative;overflow:hidden;}}
      .hover-lift {{transition:transform .28s ease,border-color .28s ease;}}
      .hover-lift:hover {{transform:translateY(-3px);}}
      p,li,[data-testid="stMarkdownContainer"] {{font-size:15px;line-height:1.65;}}
      [data-testid="stCaptionContainer"], [data-testid="stCaptionContainer"] p {{font-size:14px!important;line-height:1.55!important;color:#9b9da4!important;}}
      [data-testid="stWidgetLabel"] p,label {{font-size:15px!important;}}
      input,textarea,[data-baseweb="select"] {{font-size:16px!important;}}
      .stButton button,.stDownloadButton button {{font-size:15px!important;white-space:nowrap!important;min-width:112px;}}
      [data-baseweb="tab"] {{font-size:15px!important;}}
      [data-testid="stMetricLabel"] p {{font-size:14px!important;}}
      [data-testid="stMetricValue"] {{font-size:26px!important;line-height:1.12;}}
      .st-key-top_navigation .stButton button {{font-size:15px!important;white-space:nowrap;}}
      .yg-top-brand b {{font-size:20px;}} .yg-top-brand small {{font-size:12px;}}
      .yg-page-header small {{font-size:12px;}}
      .yg-page-header h1 {{font-size:clamp(38px,3vw,46px)!important;}}
      .yg-page-header p {{font-size:16px;line-height:1.6;}}
      h2 {{font-size:28px!important;}} h3 {{font-size:20px!important;}}
      .yg-profile {{padding:1.15rem 1.3rem;}}
      .yg-profile-copy>b {{font-size:20px;}} .yg-profile-copy>span {{font-size:15px;}} .yg-profile-copy>small {{font-size:12px;}}
      .yg-profile-stats small {{font-size:14px;}} .yg-profile-stats b {{font-size:24px;}}
      .yg-category-overview header b {{font-size:23px;}} .yg-category-overview header span {{font-size:25px;}}
      .yg-category-counts {{gap:.65rem;padding:1rem;font-size:15px;}}
      .yg-category-overview footer {{padding:.9rem 1rem;font-size:16px;}}
      [class*="st-key-work_"][class*="_info_panel"] {{position:sticky;top:5.2rem;align-self:start;}}
      [class*="st-key-work_"][class*="_info_panel"] [data-testid="stImage"] img {{max-height:520px;object-fit:contain;border-radius:15px;box-shadow:0 18px 38px rgba(0,0,0,.32);}}
      .yg-category-overview article {{position:relative;}}
      .yg-category-overview article::after,div[data-testid="stVerticalBlockBorderWrapper"]::after {{content:"";position:absolute;right:8px;top:8px;width:18px;height:18px;border-top:1px solid rgba(214,90,122,.28);border-right:1px solid rgba(214,90,122,.28);pointer-events:none;}}
      div[data-testid="stVerticalBlockBorderWrapper"] {{position:relative;}}
      .yg-section-heading small {{font-size:12px;}} .yg-section-heading h3 {{font-size:26px!important;}} .yg-section-heading>span {{font-size:14px;}}
      .yg-work-title strong {{font-size:var(--yg-font-card-title);}}
      .yg-rank-number {{padding:.34rem .52rem;font-size:14px;}}
      .yg-score-row {{gap:.38rem;margin:.5rem 0;}}
      .yg-score-row>span {{padding:.32rem .5rem;}}
      .yg-score-row small {{font-size:12px!important;}}
      .yg-review-line,.yg-grid-review {{font-size:14px;line-height:1.55;}}
      [class*="st-key-recent_row_"] [data-testid="stImage"] img,[class*="st-key-recent_finished_row_"] [data-testid="stImage"] img,[class*="st-key-ranking_"][class*="_row_"] [data-testid="stImage"] img,[class*="st-key-compare_"][class*="_row_"] [data-testid="stImage"] img,[class*="st-key-cat_"][class*="_row_"] [data-testid="stImage"] img,[class*="st-key-library_row_"] [data-testid="stImage"] img,[class*="st-key-tag_works_"][class*="_row_"] [data-testid="stImage"] img,[class*="st-key-tag_result_"] [data-testid="stImage"] img {{width:96px!important;min-width:96px;aspect-ratio:2/3;object-fit:cover;}}
      [class*="st-key-recent_row_"] [data-testid="stHorizontalBlock"]>[data-testid="stColumn"]:nth-child(2),
      [class*="st-key-recent_finished_row_"] [data-testid="stHorizontalBlock"]>[data-testid="stColumn"]:nth-child(2),
      [class*="st-key-ranking_"][class*="_row_"] [data-testid="stHorizontalBlock"]>[data-testid="stColumn"]:nth-child(2),
      [class*="st-key-compare_"][class*="_row_"] [data-testid="stHorizontalBlock"]>[data-testid="stColumn"]:nth-child(2),
      [class*="st-key-cat_"][class*="_row_"] [data-testid="stHorizontalBlock"]>[data-testid="stColumn"]:nth-child(2),
      [class*="st-key-tag_works_"][class*="_row_"] [data-testid="stHorizontalBlock"]>[data-testid="stColumn"]:nth-child(2),
      [class*="st-key-tag_result_"] [data-testid="stHorizontalBlock"]>[data-testid="stColumn"]:nth-child(2),
      [class*="st-key-library_row_"] [data-testid="stHorizontalBlock"]>[data-testid="stColumn"]:nth-child(2) {{padding-left:12px;}}
      [class*="st-key-ranking_"][class*="_row_"]>div[data-testid="stVerticalBlockBorderWrapper"],
      [class*="st-key-compare_"][class*="_row_"]>div[data-testid="stVerticalBlockBorderWrapper"],
      [class*="st-key-cat_"][class*="_row_"]>div[data-testid="stVerticalBlockBorderWrapper"],
      [class*="st-key-library_row_"]>div[data-testid="stVerticalBlockBorderWrapper"],
      [class*="st-key-tag_works_"][class*="_row_"]>div[data-testid="stVerticalBlockBorderWrapper"] {{min-height:188px;padding:1rem!important;overflow:hidden;}}
      [class*="st-key-ranking_"][class*="_row_"] [data-testid="stHorizontalBlock"],
      [class*="st-key-compare_"][class*="_row_"] [data-testid="stHorizontalBlock"],
      [class*="st-key-cat_"][class*="_row_"] [data-testid="stHorizontalBlock"],
      [class*="st-key-library_row_"] [data-testid="stHorizontalBlock"],
      [class*="st-key-tag_works_"][class*="_row_"] [data-testid="stHorizontalBlock"] {{display:grid!important;grid-template-columns:112px minmax(0,1fr) 164px!important;gap:18px!important;align-items:center!important;}}
      [class*="st-key-ranking_"][class*="_row_"] [data-testid="stHorizontalBlock"]>[data-testid="stColumn"],
      [class*="st-key-compare_"][class*="_row_"] [data-testid="stHorizontalBlock"]>[data-testid="stColumn"],
      [class*="st-key-cat_"][class*="_row_"] [data-testid="stHorizontalBlock"]>[data-testid="stColumn"],
      [class*="st-key-library_row_"] [data-testid="stHorizontalBlock"]>[data-testid="stColumn"],
      [class*="st-key-tag_works_"][class*="_row_"] [data-testid="stHorizontalBlock"]>[data-testid="stColumn"] {{width:auto!important;min-width:0!important;max-width:none!important;flex:unset!important;}}
      [class*="st-key-ranking_"][class*="_row_"] [data-testid="stHorizontalBlock"]>[data-testid="stColumn"]:nth-child(1),
      [class*="st-key-compare_"][class*="_row_"] [data-testid="stHorizontalBlock"]>[data-testid="stColumn"]:nth-child(1),
      [class*="st-key-cat_"][class*="_row_"] [data-testid="stHorizontalBlock"]>[data-testid="stColumn"]:nth-child(1),
      [class*="st-key-library_row_"] [data-testid="stHorizontalBlock"]>[data-testid="stColumn"]:nth-child(1),
      [class*="st-key-tag_works_"][class*="_row_"] [data-testid="stHorizontalBlock"]>[data-testid="stColumn"]:nth-child(1) {{width:112px!important;}}
      [class*="st-key-ranking_"][class*="_row_"] [data-testid="stHorizontalBlock"]>[data-testid="stColumn"]:nth-child(2),
      [class*="st-key-compare_"][class*="_row_"] [data-testid="stHorizontalBlock"]>[data-testid="stColumn"]:nth-child(2),
      [class*="st-key-cat_"][class*="_row_"] [data-testid="stHorizontalBlock"]>[data-testid="stColumn"]:nth-child(2),
      [class*="st-key-library_row_"] [data-testid="stHorizontalBlock"]>[data-testid="stColumn"]:nth-child(2),
      [class*="st-key-tag_works_"][class*="_row_"] [data-testid="stHorizontalBlock"]>[data-testid="stColumn"]:nth-child(2) {{min-width:0!important;padding-left:0!important;overflow:hidden;}}
      [class*="st-key-ranking_"][class*="_row_"] [data-testid="stHorizontalBlock"]>[data-testid="stColumn"]:nth-child(3),
      [class*="st-key-compare_"][class*="_row_"] [data-testid="stHorizontalBlock"]>[data-testid="stColumn"]:nth-child(3),
      [class*="st-key-cat_"][class*="_row_"] [data-testid="stHorizontalBlock"]>[data-testid="stColumn"]:nth-child(3),
      [class*="st-key-library_row_"] [data-testid="stHorizontalBlock"]>[data-testid="stColumn"]:nth-child(3),
      [class*="st-key-tag_works_"][class*="_row_"] [data-testid="stHorizontalBlock"]>[data-testid="stColumn"]:nth-child(3) {{width:164px!important;align-self:stretch;display:flex!important;align-items:center;}}
      [class*="st-key-ranking_"][class*="_row_"] .stButton button,
      [class*="st-key-compare_"][class*="_row_"] .stButton button,
      [class*="st-key-cat_"][class*="_row_"] .stButton button,
      [class*="st-key-library_row_"] .stButton button,
      [class*="st-key-tag_works_"][class*="_row_"] .stButton button {{width:100%;height:44px;min-width:0;}}
      @media(max-width:860px) {{
        [class*="st-key-ranking_"][class*="_row_"] [data-testid="stHorizontalBlock"],
        [class*="st-key-compare_"][class*="_row_"] [data-testid="stHorizontalBlock"],
        [class*="st-key-cat_"][class*="_row_"] [data-testid="stHorizontalBlock"],
        [class*="st-key-library_row_"] [data-testid="stHorizontalBlock"],
        [class*="st-key-tag_works_"][class*="_row_"] [data-testid="stHorizontalBlock"] {{grid-template-columns:92px minmax(0,1fr)!important;}}
        [class*="st-key-ranking_"][class*="_row_"] [data-testid="stHorizontalBlock"]>[data-testid="stColumn"]:nth-child(3),
        [class*="st-key-compare_"][class*="_row_"] [data-testid="stHorizontalBlock"]>[data-testid="stColumn"]:nth-child(3),
        [class*="st-key-cat_"][class*="_row_"] [data-testid="stHorizontalBlock"]>[data-testid="stColumn"]:nth-child(3),
        [class*="st-key-library_row_"] [data-testid="stHorizontalBlock"]>[data-testid="stColumn"]:nth-child(3),
        [class*="st-key-tag_works_"][class*="_row_"] [data-testid="stHorizontalBlock"]>[data-testid="stColumn"]:nth-child(3) {{grid-column:1 / -1;width:100%!important;}}
      }}
      [class*="st-key-grid_card_"]>div[data-testid="stVerticalBlockBorderWrapper"] {{min-height:420px;padding:.85rem;}}
      [class*="st-key-grid_card_"] h3 {{font-size:19px!important;line-height:1.35;}}
      [class*="st-key-grid_card_"] [data-testid="stImage"] img {{width:100%;min-height:220px;aspect-ratio:2/3;object-fit:cover;box-shadow:0 12px 28px rgba(0,0,0,.25);}}
      [class*="st-key-grid_card_"] [data-testid="stImage"] img:hover,[class*="st-key-recent_row_"] [data-testid="stImage"] img:hover {{transform:scale(1.025);filter:brightness(1.06);}}
      .yg-season-title small {{font-size:12px;}} .yg-season-title h3 {{font-size:26px!important;}} .yg-season-title>span {{font-size:14px;}}
      .yg-season-screen {{height:292px;}}
      .yg-season-window>header small {{font-size:12px;}}
      .yg-season-window>header b {{font-size:16px;line-height:1.4;}}
      .yg-season-window>header>span {{width:34px;height:34px;font-size:13px;}}
      .yg-season-empty strong {{font-size:17px;}}
      .yg-season-empty small,.yg-season-black span {{font-size:13px;}}
      .yg-season-memory {{grid-template-columns:138px 1fr;gap:1rem;padding:1rem;}}
      .yg-season-memory img {{width:138px;height:207px;border-radius:11px;box-shadow:0 10px 24px rgba(0,0,0,.24);}}
      .yg-season-memory>div>span {{font-size:13px;}} .yg-season-memory strong {{font-size:18px;line-height:1.4;}}
      .yg-season-memory small,.yg-season-memory p,.yg-season-memory em,.yg-season-memory footer {{font-size:13px;line-height:1.45;}}
      .yg-score-distribution header span {{font-size:12px;}} .yg-score-distribution b,.yg-score-distribution small {{font-size:13px;}}
      .yg-fanku-carousel {{height:430px;}}
      .yg-fanku-poster {{width:var(--yg-poster-hero);height:330px;border-radius:16px;}}
      .yg-fanku-pos-c0 {{transform:translate(-50%,-50%) scale(1.08);}}
      .yg-fanku-pos-m1 {{transform:translate(-142%,-50%) scale(.78) rotateY(7deg);}}
      .yg-fanku-pos-p1 {{transform:translate(42%,-50%) scale(.78) rotateY(-7deg);}}
      .yg-fanku-pos-m2 {{transform:translate(-222%,-50%) scale(.56) rotateY(10deg);}}
      .yg-fanku-pos-p2 {{transform:translate(122%,-50%) scale(.56) rotateY(-10deg);}}
      .yg-fanku-copy>small {{font-size:12px;}} .yg-fanku-copy h2 {{font-size:clamp(30px,3.2vw,46px)!important;}}
      .yg-fanku-copy p {{font-size:15px;}} .yg-fanku-copy b,.yg-fanku-copy span {{font-size:14px;}}
      .yg-fanku-carousel>footer {{font-size:13px;}}
      .yg-current-season-empty {{position:relative;min-height:190px;border-color:rgba(214,90,122,.38);background:radial-gradient(circle at 88% 20%,rgba(214,90,122,.1),transparent 26%),#1a1b1e;}}
      .yg-current-season-empty::after {{content:"✦  SEASONAL ARCHIVE  ◇";position:absolute;right:2rem;bottom:1.3rem;color:rgba(237,120,150,.34);font-size:13px;letter-spacing:.16em;}}
      .yg-current-season-empty small {{font-size:13px;}} .yg-current-season-empty h2 {{font-size:28px!important;}} .yg-current-season-empty p,.yg-current-season-empty>span {{font-size:14px;}}
      .yg-empty-state {{position:relative;min-height:108px;border-color:rgba(214,90,122,.34);background:radial-gradient(circle at 92% 18%,rgba(214,90,122,.09),transparent 25%),#191a1d;}}
      .yg-empty-state::after {{content:"□  LOCAL VAULT";position:absolute;right:1.2rem;bottom:.85rem;color:rgba(237,120,150,.28);font-size:12px;letter-spacing:.12em;}}
      .yg-empty-state strong {{font-size:18px;}} .yg-empty-state p {{font-size:14px!important;}}
      .yg-empty-sigil {{width:58px;height:58px;flex-basis:58px;font-size:25px;}}
      .st-key-library_filter_bar {{position:sticky;top:4.25rem;z-index:20;margin-bottom:.8rem;padding:.8rem;border:1px solid rgba(214,90,122,.2);border-radius:16px;background:rgba(21,22,25,.98);backdrop-filter:blur(14px);}}
      [class*="st-key-tag_card_"]>div[data-testid="stVerticalBlockBorderWrapper"] {{min-height:310px;padding:1rem;border-color:rgba(132,40,59,.62);background:linear-gradient(145deg,#1b1b1e,#17181b);transition:transform .25s ease,border-color .25s ease;}}
      [class*="st-key-tag_card_"]>div[data-testid="stVerticalBlockBorderWrapper"]:hover {{transform:translateY(-4px);border-color:rgba(214,90,122,.62);}}
      [class*="st-key-tag_card_"] h3 {{margin:.05rem 0 .2rem!important;font-size:20px!important;}}
      [class*="st-key-tag_card_"] [data-testid="stHorizontalBlock"] {{gap:.38rem;}}
      [class*="st-key-tag_card_"] [data-testid="stImage"] img {{width:100%;aspect-ratio:2/3;object-fit:cover;border-radius:9px;box-shadow:0 8px 18px rgba(0,0,0,.3);}}
      .yg-tag-stats {{display:grid;grid-template-columns:repeat(3,1fr);gap:.35rem;margin:.7rem 0;}}
      .yg-tag-stats span {{padding:.45rem .3rem;border:1px solid rgba(145,152,170,.13);border-radius:8px;background:#202126;color:#8f929b;font-size:12px;text-align:center;}}
      .yg-tag-stats b {{display:block;margin-top:.12rem;color:#e5e5e8;font-size:15px;}}
      .yg-tag-no-poster {{display:grid;place-items:center;height:110px;margin:.45rem 0;border:1px dashed rgba(214,90,122,.32);border-radius:10px;color:#8d6571;font-size:12px;font-weight:800;letter-spacing:.15em;}}
      .yg-tag-page {{display:grid;place-items:center;min-height:42px;color:#d66b87;font-size:14px;font-weight:800;letter-spacing:.14em;}}
      .st-key-seasonal_filters {{margin:.6rem 0 1rem;padding:.75rem;border:1px solid rgba(214,90,122,.18);border-radius:14px;background:#191a1d;}}
      .yg-season-page {{height:28px;margin:.45rem 0 .55rem;text-align:center;color:#e18aa1;font-weight:900;line-height:28px;}}
      .yg-season-stage {{position:relative;height:595px;overflow:hidden;border-radius:18px;background:radial-gradient(circle at 50% 34%,rgba(214,90,122,.12),transparent 36%),#1f2023;}}
      .yg-home-season-card {{position:absolute;top:32px;left:50%;width:230px;min-width:0;opacity:0;filter:brightness(.72);pointer-events:none;transform:translateX(-50%) scale(.58);transition:transform .45s cubic-bezier(.2,.72,.18,1),opacity .32s ease,filter .32s ease;}}
      .yg-home-season-card.pos_c {{z-index:5;opacity:1;filter:none;transform:translateX(-50%) scale(1.08);}}
      .yg-home-season-card.pos_m1 {{z-index:4;opacity:.74;transform:translateX(-174%) scale(.88);}}
      .yg-home-season-card.pos_p1 {{z-index:4;opacity:.74;transform:translateX(74%) scale(.88);}}
      .yg-home-season-card.pos_m2 {{z-index:3;opacity:.34;transform:translateX(-292%) scale(.68);}}
      .yg-home-season-card.pos_p2 {{z-index:3;opacity:.34;transform:translateX(192%) scale(.68);}}
      .yg-home-season-card.pos_c,.yg-home-season-card.pos_m1,.yg-home-season-card.pos_p1,.yg-home-season-card.pos_m2,.yg-home-season-card.pos_p2 {{pointer-events:auto;}}
      .yg-home-season-poster {{display:block;height:330px;border:1px solid rgba(214,90,122,.25);border-radius:16px;overflow:hidden;background:#121214;box-shadow:0 18px 38px rgba(0,0,0,.34);}}
      .yg-home-season-poster img {{display:block;width:100%;height:100%;object-fit:cover;}}
      .yg-season-stage.slide-next .pos_m2 {{animation:yg-season-next-m2 .72s cubic-bezier(.18,.78,.2,1) both;}}
      .yg-season-stage.slide-next .pos_m1 {{animation:yg-season-next-m1 .72s cubic-bezier(.18,.78,.2,1) both;}}
      .yg-season-stage.slide-next .pos_c {{animation:yg-season-next-c .72s cubic-bezier(.18,.78,.2,1) both;}}
      .yg-season-stage.slide-next .pos_p1 {{animation:yg-season-next-p1 .72s cubic-bezier(.18,.78,.2,1) both;}}
      .yg-season-stage.slide-next .pos_p2 {{animation:yg-season-next-p2 .72s cubic-bezier(.18,.78,.2,1) both;}}
      .yg-season-stage.slide-prev .pos_m2 {{animation:yg-season-prev-m2 .72s cubic-bezier(.18,.78,.2,1) both;}}
      .yg-season-stage.slide-prev .pos_m1 {{animation:yg-season-prev-m1 .72s cubic-bezier(.18,.78,.2,1) both;}}
      .yg-season-stage.slide-prev .pos_c {{animation:yg-season-prev-c .72s cubic-bezier(.18,.78,.2,1) both;}}
      .yg-season-stage.slide-prev .pos_p1 {{animation:yg-season-prev-p1 .72s cubic-bezier(.18,.78,.2,1) both;}}
      .yg-season-stage.slide-prev .pos_p2 {{animation:yg-season-prev-p2 .72s cubic-bezier(.18,.78,.2,1) both;}}
      @keyframes yg-season-next-m2 {{from {{opacity:.74;filter:brightness(.72);transform:translateX(-174%) scale(.88);}} to {{opacity:.34;filter:brightness(.72);transform:translateX(-292%) scale(.68);}}}}
      @keyframes yg-season-next-m1 {{from {{opacity:1;filter:none;transform:translateX(-50%) scale(1.08);}} to {{opacity:.74;filter:brightness(.72);transform:translateX(-174%) scale(.88);}}}}
      @keyframes yg-season-next-c {{from {{opacity:.74;filter:brightness(.72);transform:translateX(74%) scale(.88);}} to {{opacity:1;filter:none;transform:translateX(-50%) scale(1.08);}}}}
      @keyframes yg-season-next-p1 {{from {{opacity:.34;filter:brightness(.72);transform:translateX(192%) scale(.68);}} to {{opacity:.74;filter:brightness(.72);transform:translateX(74%) scale(.88);}}}}
      @keyframes yg-season-next-p2 {{from {{opacity:0;filter:brightness(.72);transform:translateX(260%) scale(.58);}} to {{opacity:.34;filter:brightness(.72);transform:translateX(192%) scale(.68);}}}}
      @keyframes yg-season-prev-m2 {{from {{opacity:0;filter:brightness(.72);transform:translateX(-360%) scale(.58);}} to {{opacity:.34;filter:brightness(.72);transform:translateX(-292%) scale(.68);}}}}
      @keyframes yg-season-prev-m1 {{from {{opacity:.34;filter:brightness(.72);transform:translateX(-292%) scale(.68);}} to {{opacity:.74;filter:brightness(.72);transform:translateX(-174%) scale(.88);}}}}
      @keyframes yg-season-prev-c {{from {{opacity:.74;filter:brightness(.72);transform:translateX(-174%) scale(.88);}} to {{opacity:1;filter:none;transform:translateX(-50%) scale(1.08);}}}}
      @keyframes yg-season-prev-p1 {{from {{opacity:1;filter:none;transform:translateX(-50%) scale(1.08);}} to {{opacity:.74;filter:brightness(.72);transform:translateX(74%) scale(.88);}}}}
      @keyframes yg-season-prev-p2 {{from {{opacity:.74;filter:brightness(.72);transform:translateX(74%) scale(.88);}} to {{opacity:.34;filter:brightness(.72);transform:translateX(192%) scale(.68);}}}}
      .yg-home-season-card h3 {{margin:13px 0 4px;font-size:19px;line-height:1.32;color:#eee;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;}}
      .yg-season-original {{height:19px;color:#8f9199;font-size:12px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;}}
      .yg-season-meta {{margin-top:6px;color:#aaa;font-size:12px;}}
      .yg-season-actions {{display:flex;gap:7px;margin-top:8px;}}
      .yg-season-actions a {{display:grid;place-items:center;flex:1;height:33px;border:1px solid #34353a;border-radius:9px;background:#202126;color:#aaa;text-decoration:none;font-size:17px;}}
      .yg-season-actions .seen-active {{background:#a52d4e;color:white;border-color:#ff7699;}}
      .yg-season-actions .watching-active {{background:#267b55;color:white;border-color:#55c98c;}}
      .yg-season-actions .abandon-active {{background:#9a3030;color:white;border-color:#ef6b6b;}}
      .yg-season-state {{height:18px;margin-top:6px;font-size:12px;font-weight:800;text-align:center;color:#ff8baa;}}
      .st-key-season_carousel_shell [data-testid="stHorizontalBlock"] {{align-items:center!important;}}
      [class*="st-key-season_carousel_index_"][class*="_prev"],[class*="st-key-season_carousel_index_"][class*="_next"] {{display:flex!important;justify-content:center!important;overflow:visible!important;}}
      [class*="st-key-season_carousel_index_"][class*="_prev"] button,[class*="st-key-season_carousel_index_"][class*="_next"] button {{width:82px!important;min-width:82px!important;height:92px!important;padding:0!important;border-color:rgba(214,90,122,.36)!important;border-radius:18px!important;background:#202126!important;color:#d8d8dc!important;font-size:34px!important;font-weight:900!important;line-height:1!important;box-shadow:0 14px 32px rgba(0,0,0,.22)!important;}}
      [class*="st-key-season_candidate_"]>div[data-testid="stVerticalBlockBorderWrapper"] {{min-height:485px;padding:.72rem;border-color:rgba(214,90,122,.2);background:#191a1d;transition:transform .36s cubic-bezier(.22,.8,.3,1),opacity .36s ease,border-color .25s ease;}}
      [class*="st-key-season_candidate_"] [data-testid="stImage"] img {{width:100%;aspect-ratio:2/3;object-fit:cover;border-radius:11px;}}
      [class*="st-key-season_candidate_0_"],[class*="st-key-season_candidate_4_"] {{opacity:.46;transform:scale(.88);}}
      [class*="st-key-season_candidate_1_"],[class*="st-key-season_candidate_3_"] {{opacity:.76;transform:scale(.95);}}
      [class*="st-key-season_candidate_2_"] {{z-index:3;transform:scale(1.055);}}
      [class*="st-key-season_candidate_2_"]>div[data-testid="stVerticalBlockBorderWrapper"] {{border-color:rgba(214,90,122,.62);box-shadow:0 18px 38px rgba(0,0,0,.32);}}
      [class*="st-key-watching_active_"] button {{border-color:#d65a7a!important;background:#9d2949!important;color:#fff!important;}}
      [class*="st-key-planned_active_"] button {{border-color:#4aa7d9!important;background:#246d94!important;color:#fff!important;}}
      [class*="st-key-abandon_active_"] button {{border-color:#111!important;background:#090909!important;color:#fff!important;}}
      [class*="st-key-watching_idle_"] button,[class*="st-key-planned_idle_"] button,[class*="st-key-abandon_idle_"] button {{color:#97999f!important;}}
      [class*="st-key-bgm_rank_seen_active_"] button {{border-color:#ff7699!important;background:#a52d4e!important;color:#fff!important;}}
      [class*="st-key-bgm_rank_watch_active_"] button {{border-color:#55c98c!important;background:#267b55!important;color:#fff!important;}}
      [class*="st-key-bgm_rank_abandon_active_"] button {{border-color:#ef6b6b!important;background:#9a3030!important;color:#fff!important;}}
      [class*="st-key-bgm_rank_seen_idle_"] button,[class*="st-key-bgm_rank_watch_idle_"] button,[class*="st-key-bgm_rank_abandon_idle_"] button {{color:#a5a7ad!important;}}
      [class*="st-key-bangumi_rank_card_"]>div[data-testid="stVerticalBlockBorderWrapper"] {{height:620px!important;min-height:620px!important;padding:.85rem!important;overflow:hidden!important;border:1px solid rgba(214,90,122,.16)!important;border-radius:8px!important;background:#1a1b1e!important;}}
      [class*="st-key-bangumi_rank_card_"]>div[data-testid="stVerticalBlockBorderWrapper"]>div[data-testid="stVerticalBlock"] {{display:flex!important;height:100%!important;min-height:0!important;flex-direction:column!important;}}
      [class*="st-key-bangumi_rank_card_"] .yg-bangumi-cover {{display:flex;align-items:center;justify-content:center;width:100%;height:342px;min-height:342px;overflow:hidden;border-radius:12px;background:transparent;}}
      [class*="st-key-bangumi_rank_card_"] .yg-bangumi-cover img {{display:block;width:100%;height:100%;object-fit:contain;object-position:center center;border-radius:12px;}}
      [class*="st-key-bangumi_rank_card_"] .yg-bgm-rank-copy {{display:flex;height:132px;min-height:132px;flex-direction:column;gap:.42rem;margin:.72rem 0 .42rem;overflow:hidden;}}
      [class*="st-key-bangumi_rank_card_"] .yg-bgm-rank-copy strong {{display:-webkit-box;height:50px;overflow:hidden;color:#ededf0;-webkit-box-orient:vertical;-webkit-line-clamp:2;font-size:18px;line-height:1.38;}}
      [class*="st-key-bangumi_rank_card_"] .yg-bgm-rank-copy small {{height:21px;overflow:hidden;color:#8d8f96;white-space:nowrap;text-overflow:ellipsis;font-size:13px;line-height:21px;}}
      [class*="st-key-bangumi_rank_card_"] .yg-bgm-rank-copy b {{height:24px;overflow:hidden;color:#dedee2;font-size:15px;line-height:24px;white-space:nowrap;text-overflow:ellipsis;}}
      [class*="st-key-bangumi_rank_card_"] [data-testid="stHorizontalBlock"] {{margin-top:auto!important;gap:.45rem;}}
      [class*="st-key-bangumi_rank_card_"] .stButton button {{height:42px;width:100%;min-width:0;}}
      [class*="st-key-bangumi_rank_card_"] .yg-bgm-rank-status {{height:24px;margin-top:.34rem;overflow:hidden;color:#92949b;font-size:13px;font-weight:800;line-height:24px;white-space:nowrap;text-align:center;}}
      [class*="st-key-bangumi_rank_card_"] [data-testid="stCaptionContainer"] {{height:0!important;margin:0!important;overflow:hidden!important;}}
      .yg-bangumi-rank-page {{display:grid;place-items:center;min-height:42px;color:#d66b87;font-size:14px;font-weight:850;letter-spacing:.08em;}}
      .yg-bg-controls {{display:none;}}
      /* 第 17/17 步收尾：首页使用番库式紧凑首屏，个人档案与美图固定在同一视觉区块。 */
      .block-container {{width:100%!important;max-width:calc(100vw - 80px)!important;min-width:0!important;padding-top:3.35rem!important;box-sizing:border-box!important;}}
      [data-testid="stAppViewContainer"],section.main,.main {{width:100vw!important;max-width:100vw!important;overflow-x:hidden!important;}}
      .block-container {{width:calc(100vw - 80px)!important;max-width:calc(100vw - 80px)!important;margin:0 auto!important;padding-right:0!important;padding-left:0!important;}}
      .st-key-top_navigation {{width:100vw!important;max-width:100vw!important;left:0!important;right:0!important;}}
      .st-key-top_navigation>div {{width:calc(100vw - 80px)!important;max-width:calc(100vw - 80px)!important;margin:0 auto!important;padding-right:0!important;padding-left:0!important;}}
      .st-key-home_profile_area {{margin:0 0 .35rem;padding:1rem;border:1px solid rgba(214,90,122,.16);border-radius:20px;background:linear-gradient(135deg,rgba(29,30,33,.98),rgba(24,25,28,.96));overflow:hidden;}}
      .st-key-home_profile_area [data-testid="stHorizontalBlock"] {{align-items:stretch!important;gap:1rem;}}
      .st-key-home_profile_area [data-testid="stHorizontalBlock"]>* {{min-width:0!important;}}
      .st-key-home_profile_area iframe {{display:block;width:100%!important;max-width:100%;height:370px!important;border:0;}}
      .st-key-home_profile_area [data-testid="stColumn"] {{display:flex;min-width:0;flex-direction:column;align-self:stretch;}}
      .st-key-home_profile_area [data-testid="stColumn"]>div,
      .st-key-home_profile_area [data-testid="stColumn"]>div>div,
      .st-key-home_profile_area [data-testid="stColumn"] [data-testid="stVerticalBlock"] {{height:100%;}}
      .st-key-home_profile_area .yg-profile {{display:flex;align-items:flex-start;flex:1 1 auto;flex-direction:column;margin:.25rem 0 0;padding:.85rem;min-height:365px;}}
      .st-key-home_profile_area .yg-profile-avatar {{display:none;}}
      .st-key-home_profile_area .yg-profile-copy {{width:100%;}}
      .st-key-home_profile_area .yg-profile-copy>b {{font-size:28px!important;}}
      .st-key-home_profile_area .yg-profile-copy>span {{font-size:18px!important;}}
      .st-key-home_profile_area .yg-profile-copy>small {{font-size:12px!important;}}
      .st-key-home_profile_area .yg-profile-stats {{grid-template-columns:repeat(3,minmax(0,1fr));width:100%;margin-top:1.15rem;}}
      .st-key-home_profile_area .yg-profile-stats>div {{min-height:88px;padding:.9rem 1.05rem;}}
      .st-key-home_profile_area .yg-profile-stats small {{font-size:15px!important;}}
      .st-key-home_profile_area .yg-profile-stats b {{font-size:31px!important;}}
      .yg-home-kicker {{margin:.05rem 0 .35rem;color:#df6d8b;font-size:12px;font-weight:900;letter-spacing:.16em;}}
      .yg-home-title {{margin:0!important;background:none!important;color:#f4f4f6!important;-webkit-text-fill-color:#f4f4f6!important;font-size:clamp(38px,3.05vw,56px)!important;line-height:1.04;text-shadow:0 8px 30px rgba(0,0,0,.28)!important;}}
      .yg-home-subtitle {{margin:.62rem 0 .85rem;color:#a8aab0;font-size:17px!important;}}
      .yg-art-empty {{display:grid;place-items:center;min-height:250px;padding:1rem;border:1px dashed #3d3e43;border-radius:15px;background:#1d1e21;color:#8c8e95;text-align:center;}}
      [class*="st-key-ranking_podium_"] {{margin:.75rem 0 1rem;padding:1.35rem 1rem 1.9rem;border:1px solid rgba(229,170,74,.24);border-radius:20px;background:radial-gradient(circle at 50% 0,rgba(235,178,78,.12),transparent 45%),#18191c;}}
      .yg-podium-kicker {{margin:.25rem 0 1.05rem;color:#d9a94f;font-size:12px;font-weight:900;letter-spacing:.18em;text-align:center;}}
      [class*="st-key-podium_card_"]>div[data-testid="stVerticalBlockBorderWrapper"] {{padding:.9rem;border-color:rgba(229,170,74,.3);background:linear-gradient(155deg,#232126,#17181b);box-shadow:0 16px 34px rgba(0,0,0,.28);}}
      [class*="st-key-podium_card_10_2_"],[class*="st-key-podium_card_10_3_"] {{position:relative;z-index:1;height:auto!important;min-height:760px!important;margin-top:30px;overflow:visible;transform:none;}}
      [class*="st-key-podium_card_10_2_"]>div[data-testid="stVerticalBlockBorderWrapper"],[class*="st-key-podium_card_10_3_"]>div[data-testid="stVerticalBlockBorderWrapper"] {{height:auto!important;min-height:760px!important;box-sizing:border-box;overflow:visible;}}
      [class*="st-key-podium_card_10_1_"] {{position:relative;z-index:3;transform:none;}}
      [class*="st-key-podium_card_10_1_"]>div[data-testid="stVerticalBlockBorderWrapper"] {{border-color:rgba(245,196,88,.46);box-shadow:0 22px 48px rgba(0,0,0,.34),0 0 34px rgba(229,170,74,.12);}}
      [class*="st-key-podium_image_10_"] {{width:100%!important;align-items:stretch!important;}}
      [class*="st-key-podium_image_10_"] [data-testid="stElementContainer"],
      [class*="st-key-podium_image_10_"] [data-testid="stFullScreenFrame"],
      [class*="st-key-podium_image_10_"] [data-testid="stImage"],
      [class*="st-key-podium_image_10_"] [data-testid="stImageContainer"] {{width:100%!important;max-width:100%!important;display:grid!important;place-items:center!important;}}
      [class*="st-key-podium_card_"] [data-testid="stImage"] {{display:grid;place-items:center;background:transparent;}}
      [class*="st-key-podium_card_"] [data-testid="stImage"] img {{aspect-ratio:2/3;max-height:365px;object-fit:contain;border-radius:14px;}}
      [class*="st-key-podium_card_10_"] [data-testid="stImage"] img {{height:430px!important;max-height:430px!important;object-fit:contain!important;object-position:center center!important;}}
      [class*="st-key-podium_card_10_1_"] [data-testid="stImage"] img {{height:520px!important;max-height:520px!important;object-fit:contain!important;object-position:center center!important;}}
      [class*="st-key-podium_image_10_2_"] [data-testid="stImage"] img,[class*="st-key-podium_image_10_3_"] [data-testid="stImage"] img {{display:block;width:100%!important;height:430px!important;max-height:430px!important;object-fit:contain!important;object-position:center center!important;border-radius:14px;}}
      [class*="st-key-podium_image_10_1_"] [data-testid="stImage"] img {{display:block;width:100%!important;height:540px!important;max-height:540px!important;object-fit:contain!important;object-position:center center!important;border-radius:14px;}}
      [class*="st-key-podium_image_10_1_"] [data-testid="stImage"] {{margin-top:-.3rem;}}
      .yg-podium-medal {{display:flex;align-items:center;gap:.45rem;margin-bottom:.55rem;font-weight:900;}}
      .yg-podium-medal b {{display:grid;place-items:center;width:34px;height:34px;border-radius:50%;font-size:18px;}}
      .yg-podium-medal.rank-1 {{color:#f2c75e}} .yg-podium-medal.rank-1 b {{width:42px;height:42px;background:linear-gradient(135deg,#ffe88a,#b57812);color:#211707;box-shadow:0 0 24px rgba(255,201,75,.34);}}
      .yg-podium-medal.rank-2 {{color:#d9dde4}} .yg-podium-medal.rank-2 b {{background:linear-gradient(135deg,#f2f4f7,#7c8490);color:#20242a;box-shadow:0 0 18px rgba(205,216,230,.22);}}
      .yg-podium-medal.rank-3 {{color:#d99561}} .yg-podium-medal.rank-3 b {{background:linear-gradient(135deg,#e7a26d,#74401f);color:#24140b;box-shadow:0 0 18px rgba(194,107,55,.22);}}
      .yg-podium-score {{margin:.45rem 0;color:#ffd46e;font-size:32px;font-weight:900;line-height:1;}}
      .yg-podium-score small {{display:block;margin-top:.25rem;color:#858790;font-size:9px;letter-spacing:.16em;}}
      .yg-ranking-tier-title {{margin:1rem 0 .7rem;padding:.8rem 1rem;border-left:4px solid #d65a7a;border-radius:8px;background:#202126;color:#eee;font-weight:900;letter-spacing:.06em;}}
      .yg-ranking-tier-title.tier-10 {{border:1px solid rgba(232,184,77,.3);border-left:5px solid #e8b84d;background:linear-gradient(90deg,rgba(119,78,17,.32),#1b1c20 62%);color:#f4d783;box-shadow:0 12px 30px rgba(0,0,0,.18);}}
      .yg-ranking-tier-title.tier-20 {{border-color:#7d89b8;background:#1d2028;}}
      .yg-ranking-tier-title.tier-50 {{border-color:#64676f;background:#1d1e21;color:#b9bbc2;}}
      .yg-ranking-tier-title.tier-100 {{border-color:#44474e;background:#191a1d;color:#989ba3;}}
      [class*="st-key-ranking_ten_card_"]>div[data-testid="stVerticalBlockBorderWrapper"] {{height:800px;min-height:800px;padding:.8rem;border-color:rgba(232,184,77,.34);background:linear-gradient(165deg,#29251d,#17181b 58%);box-shadow:0 18px 38px rgba(0,0,0,.3);overflow:hidden;}}
      [class*="st-key-ranking_ten_card_"]>div[data-testid="stVerticalBlockBorderWrapper"]>div[data-testid="stVerticalBlock"] {{display:flex;height:100%;min-height:0;flex-direction:column;}}
      [class*="st-key-ranking_ten_card_"] [data-testid="stImage"] {{display:grid;place-items:center;height:512px;min-height:512px;border-radius:14px;background:transparent;overflow:hidden;}}
      [class*="st-key-ranking_ten_card_"] [data-testid="stImage"] img {{display:block;width:100%;height:512px!important;max-height:512px!important;object-fit:contain;object-position:center center;border-radius:14px;box-shadow:0 16px 30px rgba(0,0,0,.36);}}
      [class*="st-key-ranking_ten_card_"] h3 {{display:-webkit-box;height:62px;min-height:62px;overflow:hidden;margin:.55rem 0 .15rem!important;-webkit-box-orient:vertical;-webkit-line-clamp:2;font-size:18px!important;line-height:1.35;}}
      .yg-ten-rank {{color:#e8bd59;font-size:12px;font-weight:900;letter-spacing:.16em;}}
      .yg-ten-score {{height:58px;margin:.2rem 0 .55rem;color:#ffd66f;font-size:28px;font-weight:900;line-height:1;}}
      .yg-ten-score small {{display:block;margin-top:.3rem;color:#8e8a80;font-size:9px;letter-spacing:.15em;}}
      [class*="st-key-ranking_ten_card_"] .stButton {{margin-top:auto;}}
      [class*="st-key-ranking_ten_card_"] .stButton button {{height:48px;width:100%;}}
      [class*="st-key-ranking_twenty_card_"]>div[data-testid="stVerticalBlockBorderWrapper"] {{min-height:430px;padding:.7rem;border-color:rgba(111,132,190,.32);background:linear-gradient(180deg,#222630,#191a1e);}}
      [class*="st-key-ranking_twenty_card_"] [data-testid="stImage"] {{display:grid;place-items:center;background:transparent;}}
      [class*="st-key-ranking_twenty_card_"] [data-testid="stImage"] img {{aspect-ratio:2/3;max-height:285px;object-fit:contain;object-position:center center;border-radius:10px;filter:saturate(.9);}}
      [class*="st-key-ranking_twenty_card_"] h3 {{min-height:46px;margin:.25rem 0 .05rem!important;font-size:16px!important;}}
      .yg-twenty-rank {{margin-bottom:.35rem;color:#9aa8d4;font-size:11px;font-weight:850;letter-spacing:.14em;}}
      .yg-twenty-score {{display:flex;align-items:baseline;justify-content:space-between;margin:.25rem 0 .55rem;}}
      .yg-twenty-score b {{color:#c5d0f0;font-size:22px;}} .yg-twenty-score span {{color:#777d8d;font-size:10px;}}
      [class*="st-key-ranking_fifty_card_"]>div[data-testid="stVerticalBlockBorderWrapper"] {{min-height:205px;padding:.65rem;border-color:#35373d;background:#1b1c1f;box-shadow:none;}}
      [class*="st-key-ranking_fifty_card_"] [data-testid="stImage"] {{display:grid;place-items:center;width:100%;height:166px;min-height:166px;background:transparent;overflow:hidden;}}
      [class*="st-key-ranking_fifty_card_"] [data-testid="stImage"] img {{display:block;width:100%;height:166px!important;max-height:166px!important;object-fit:contain!important;object-position:center center!important;border-radius:8px;filter:none;}}
      [class*="st-key-ranking_fifty_card_"] h3 {{margin:.1rem 0!important;font-size:16px!important;}}
      .yg-fifty-rank {{color:#8c9099;font-size:11px;font-weight:850;letter-spacing:.12em;}}
      .yg-fifty-score {{display:flex;align-items:baseline;gap:.45rem;margin:.25rem 0 .4rem;}}
      .yg-fifty-score b {{color:#ddd;font-size:21px;}} .yg-fifty-score span {{color:#777;font-size:9px;letter-spacing:.1em;}} .yg-fifty-score em {{margin-left:auto;color:#858890;font-size:10px;font-style:normal;}}
      .st-key-home_profile_area + div {{margin-top:0!important;}}
      .st-key-seasonal_filters {{margin:.35rem 0 .65rem!important;}}
      .st-key-seasonal_filters [data-testid="stHorizontalBlock"] {{display:grid!important;grid-template-columns:minmax(420px,1.15fr) minmax(300px,.85fr) minmax(420px,1.35fr)!important;gap:1rem!important;align-items:end!important;}}
      .st-key-seasonal_filters [data-testid="stHorizontalBlock"]>[data-testid="stColumn"] {{width:auto!important;min-width:0!important;max-width:none!important;flex:unset!important;}}
      .st-key-season_status_filter [data-baseweb="button-group"] {{display:grid!important;grid-template-columns:repeat(6,minmax(58px,1fr))!important;}}
      .st-key-season_status_filter button {{min-width:0!important;}}
      .yg-fanku-carousel {{margin-top:.15rem!important;}}
      /* Read-only archive alignment: keep poster, copy, metadata and action on fixed tracks. */
      .st-key-home_recent_grid [class*="st-key-recent_row_"]>div[data-testid="stVerticalBlockBorderWrapper"],
      .st-key-home_recent_grid [class*="st-key-recent_finished_row_"]>div[data-testid="stVerticalBlockBorderWrapper"] {{height:278px;min-height:278px;padding:1rem;overflow:hidden;}}
      .st-key-home_recent_grid [class*="st-key-recent_row_"]>div[data-testid="stVerticalBlockBorderWrapper"]>div[data-testid="stVerticalBlock"],
      .st-key-home_recent_grid [class*="st-key-recent_finished_row_"]>div[data-testid="stVerticalBlockBorderWrapper"]>div[data-testid="stVerticalBlock"] {{height:100%;}}
      .st-key-home_recent_grid [class*="st-key-recent_row_"] [data-testid="stHorizontalBlock"],
      .st-key-home_recent_grid [class*="st-key-recent_finished_row_"] [data-testid="stHorizontalBlock"] {{display:grid!important;grid-template-columns:clamp(104px,7.1vw,136px) minmax(0,1fr) clamp(126px,8.85vw,170px)!important;gap:clamp(14px,1.25vw,28px)!important;height:100%;align-items:center!important;}}
      .st-key-home_recent_grid [class*="st-key-recent_row_"] [data-testid="stHorizontalBlock"]>[data-testid="stColumn"],
      .st-key-home_recent_grid [class*="st-key-recent_finished_row_"] [data-testid="stHorizontalBlock"]>[data-testid="stColumn"] {{width:auto!important;min-width:0!important;max-width:none!important;flex:unset!important;}}
      .st-key-home_recent_grid [class*="st-key-recent_row_"] [data-testid="stHorizontalBlock"]>[data-testid="stColumn"]:nth-child(1),
      .st-key-home_recent_grid [class*="st-key-recent_finished_row_"] [data-testid="stHorizontalBlock"]>[data-testid="stColumn"]:nth-child(1) {{width:clamp(104px,7.1vw,136px)!important;display:flex!important;justify-content:center!important;}}
      .st-key-home_recent_grid [class*="st-key-recent_row_"] [data-testid="stHorizontalBlock"]>[data-testid="stColumn"]:nth-child(2),
      .st-key-home_recent_grid [class*="st-key-recent_finished_row_"] [data-testid="stHorizontalBlock"]>[data-testid="stColumn"]:nth-child(2) {{min-width:0!important;padding-left:0!important;overflow:hidden;}}
      .st-key-home_recent_grid [class*="st-key-recent_row_"] [data-testid="stHorizontalBlock"]>[data-testid="stColumn"]:nth-child(3),
      .st-key-home_recent_grid [class*="st-key-recent_finished_row_"] [data-testid="stHorizontalBlock"]>[data-testid="stColumn"]:nth-child(3) {{width:clamp(126px,8.85vw,170px)!important;align-self:stretch;display:flex!important;align-items:center!important;}}
      .st-key-home_recent_grid .yg-work-title {{min-width:0;min-height:31px;}}
      .st-key-home_recent_grid .yg-work-title strong {{display:-webkit-box;overflow:hidden;-webkit-box-orient:vertical;-webkit-line-clamp:1;}}
      .st-key-home_recent_grid .yg-score-row {{flex-wrap:nowrap;gap:clamp(.2rem,.3vw,.38rem);}}
      .st-key-home_recent_grid .yg-score-row>span {{min-width:0;padding:.32rem clamp(.3rem,.4vw,.5rem);}}
      .st-key-home_recent_grid .yg-score-row small {{font-size:clamp(10px,.63vw,12px)!important;}}
      .st-key-home_recent_grid .yg-score-row b {{font-size:clamp(13px,.79vw,15px)!important;}}
      .st-key-home_recent_grid .yg-work-original {{height:25px;overflow:hidden;white-space:nowrap;text-overflow:ellipsis;}}
      .st-key-home_recent_grid .yg-work-votes {{height:25px;overflow:hidden;white-space:nowrap;text-overflow:ellipsis;}}
      .st-key-home_recent_grid .yg-work-tags {{height:31px;overflow:hidden;white-space:nowrap;}}
      .st-key-home_recent_grid .yg-review-line {{height:23px;overflow:hidden;white-space:nowrap;text-overflow:ellipsis;}}
      .st-key-home_recent_grid .yg-work-finish {{height:22px;overflow:hidden;white-space:nowrap;text-overflow:ellipsis;}}
      .st-key-home_recent_grid [data-testid="stImage"] img {{width:clamp(88px,6vw,112px)!important;min-width:clamp(88px,6vw,112px);height:auto!important;aspect-ratio:2/3;object-fit:cover;}}

      [class*="_grid_card_"]>div[data-testid="stVerticalBlockBorderWrapper"] {{height:548px;min-height:548px;padding:.9rem;overflow:hidden;}}
      [class*="_grid_card_"]>div[data-testid="stVerticalBlockBorderWrapper"]>div[data-testid="stVerticalBlock"] {{display:flex;height:100%;flex-direction:column;}}
      [class*="_grid_card_"] [data-testid="stHorizontalBlock"] {{min-height:422px;align-items:stretch;}}
      [class*="_grid_card_"] [data-testid="stImage"] img {{width:100%;height:330px!important;min-height:330px;aspect-ratio:2/3;object-fit:cover;border-radius:12px;}}
      [class*="_grid_card_"] .stButton {{margin-top:auto;}}
      [class*="_grid_card_"] .stButton button {{height:48px;width:100%;}}
      [class*="st-key-library_grid_grid_card_"]>div[data-testid="stVerticalBlockBorderWrapper"] {{height:430px;min-height:430px;padding:.85rem;overflow:hidden;}}
      [class*="st-key-library_grid_grid_card_"] [data-testid="stHorizontalBlock"] {{min-height:326px;align-items:flex-start;}}
      [class*="st-key-library_grid_grid_card_"] [data-testid="stImage"] {{display:grid;place-items:center;height:326px;border-radius:12px;background:transparent;overflow:hidden;}}
      [class*="st-key-library_grid_grid_card_"] [data-testid="stImage"] img {{display:block;width:100%;height:326px!important;min-height:326px;object-fit:contain!important;object-position:center center!important;border-radius:12px;}}
      [class*="st-key-library_grid_grid_card_"] .stButton {{margin-top:.55rem;}}
      [class*="st-key-tag_works_"][class*="_grid_card_"]>div[data-testid="stVerticalBlockBorderWrapper"] {{height:392px;min-height:392px;padding:.85rem;overflow:hidden;}}
      [class*="st-key-tag_works_"][class*="_grid_card_"] [data-testid="stHorizontalBlock"] {{min-height:278px;}}
      [class*="st-key-tag_works_"][class*="_grid_card_"] [data-testid="stImage"] img {{height:278px!important;min-height:278px;}}
      .yg-grid-title {{display:-webkit-box;height:54px;overflow:hidden;-webkit-box-orient:vertical;-webkit-line-clamp:2;font-size:19px;font-weight:800;line-height:1.4;}}
      .yg-grid-original {{display:-webkit-box;height:44px;overflow:hidden;-webkit-box-orient:vertical;-webkit-line-clamp:2;color:#858890;line-height:1.5;}}
      .yg-grid-votes {{height:25px;overflow:hidden;color:#858890;white-space:nowrap;text-overflow:ellipsis;}}
      .yg-grid-tags {{height:58px;overflow:hidden;align-content:flex-start;}}
      .yg-grid-review {{height:24px;overflow:hidden;white-space:nowrap;text-overflow:ellipsis;}}
      @media (max-width:640px) {{.yg-poster-rail {{display:none;}}}}
      @media (max-width:640px) {{.yg-fanku-pos-m2,.yg-fanku-pos-p2,.yg-home-season-card.pos_m2,.yg-home-season-card.pos_p2 {{opacity:0;visibility:hidden;}}.yg-fanku-stage {{transform:scale(.96);}}[class*="st-key-season_candidate_0_"],[class*="st-key-season_candidate_4_"] {{display:none;}}}}
      @media (max-width:640px) {{.yg-fanku-pos-m1,.yg-fanku-pos-p1 {{opacity:0;visibility:hidden;}}.yg-fanku-scene {{grid-template-columns:48% 52%;}}}}
      @media (max-width:640px) {{.yg-profile {{align-items:flex-start;flex-wrap:wrap;}}.yg-profile-stats {{grid-template-columns:repeat(3,1fr);width:100%;flex-basis:100%;}}.yg-category-overview,.yg-season-grid {{grid-template-columns:repeat(2,minmax(0,1fr));}}.yg-fanku-scene {{grid-template-columns:38% 62%;}}.yg-fanku-copy {{padding-left:1.4rem;}}.st-key-home_profile_area [data-testid="stHorizontalBlock"] {{flex-direction:column;}}}}
      @media (max-width:640px) {{[class*="_grid_card_"]>div[data-testid="stVerticalBlockBorderWrapper"] {{height:auto;min-height:520px;}}.st-key-home_recent_grid [class*="st-key-recent_row_"]>div[data-testid="stVerticalBlockBorderWrapper"],.st-key-home_recent_grid [class*="st-key-recent_finished_row_"]>div[data-testid="stVerticalBlockBorderWrapper"] {{height:auto;min-height:260px;}}}}
      @media (max-width:640px) {{[data-testid="stSidebar"] {{display:block;}}.st-key-top_navigation {{display:none;}}.block-container {{min-width:0!important;padding-top:1.2rem;}}.yg-profile-stats {{grid-template-columns:repeat(2,1fr);}}.yg-category-overview,.yg-season-grid {{grid-template-columns:1fr;}}.yg-bg-grid {{grid-template-columns:repeat(4,1fr);}}.yg-fanku-carousel {{height:420px;}}.yg-fanku-scene {{grid-template-columns:1fr;}}.yg-fanku-copy {{justify-content:flex-start;padding:1.2rem;}}.yg-fanku-stage {{margin-top:120px;}}.yg-season-stage {{height:500px;}}.yg-home-season-card {{display:none;}}.yg-home-season-card.pos_c {{display:block;width:min(72vw,240px);}}.yg-home-season-poster {{height:320px;}}[class*="st-key-work_"][class*="_info_panel"] {{position:static;}}}}
      @media (prefers-reduced-motion:reduce) {{*,*::before,*::after {{animation-duration:.01ms!important; animation-iteration-count:1!important; transition-duration:.01ms!important; scroll-behavior:auto!important;}}}}
    </style>
    """, unsafe_allow_html=True)
