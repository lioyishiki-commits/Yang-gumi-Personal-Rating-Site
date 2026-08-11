"""Shared score, difference, tag, year and null-safe sorting helpers."""
from __future__ import annotations

import re
from typing import Any, Iterable


SCORE_RANGES = [f"{value / 2:.2f} 到 {value / 2 + 0.5:.2f}" for value in range(1, 18)] + ["9.00 分以上"]
DIFF_ABS_RANGES = [
    "0.00 到 0.50", "0.50 到 1.00", "1.00 到 1.50", "1.50 到 2.00",
    "2.00 到 2.50", "2.50 到 3.00", "3.00 以上",
]
DIFF_DIRECTIONS = ["全部", "我高于 Bangumi", "我低于 Bangumi", "基本一致"]
COMPARE_BIAS_OPTIONS = ["全部", "偏高", "偏低", "接近"]
PERIOD_AVERAGE_MODES = ["单季度", "单年", "年代范围"]
PERIOD_QUARTERS = [4, 3, 2, 1]


def get_score_ranges(include_all: bool = False) -> list[str]:
    ranges = list(SCORE_RANGES)
    return (["全部"] + ranges) if include_all else ranges


def calculate_score_diff(item: dict[str, Any]) -> float | None:
    mine, public = item.get("score_total"), item.get("bangumi_score")
    if mine is None or public is None:
        return None
    return round(float(mine) - float(public), 2)


def format_diff(value: Any) -> str:
    return "—" if value is None else f"{float(value):+.2f}"


def _bounds(label: str) -> tuple[float, float | None]:
    normalized = label.replace("–", " 到 ").replace("—", " 到 ")
    numbers = [float(value) for value in re.findall(r"\d+(?:\.\d+)?", normalized)]
    if "以上" in normalized:
        return numbers[0], None
    if len(numbers) != 2:
        raise ValueError(f"无法识别区间：{label}")
    return numbers[0], numbers[1]


def score_in_range(value: Any, selected_range: str) -> bool:
    if selected_range == "全部":
        return True
    if value is None or value == "":
        return False
    low, high = _bounds(selected_range)
    score = float(value)
    return score >= low if high is None else low <= score < high


def apply_score_range_filter(
    items: Iterable[dict[str, Any]], field_name: str, selected_range: str
) -> list[dict[str, Any]]:
    return [item for item in items if score_in_range(item.get(field_name), selected_range)]


def diff_direction_matches(value: Any, direction: str) -> bool:
    if direction == "全部":
        return True
    if value is None or value == "":
        return False
    diff = float(value)
    if direction == "我高于 Bangumi":
        return diff > 0
    if direction == "我低于 Bangumi":
        return diff < 0
    if direction == "基本一致":
        return -0.5 <= diff <= 0.5
    return True


def compare_bias_matches(value: Any, selected_bias: str) -> bool:
    """Match the mutually exclusive score-bias groups used on the compare page."""
    if selected_bias == "全部":
        return True
    if value is None or value == "":
        return False
    diff = float(value)
    if selected_bias == "偏高":
        return diff > 0.5
    if selected_bias == "偏低":
        return diff < -0.5
    if selected_bias == "接近":
        return -0.5 <= diff <= 0.5
    return False


def diff_abs_in_range(value: Any, selected_range: str) -> bool:
    if selected_range == "全部":
        return True
    if value is None or value == "":
        return False
    low, high = _bounds(selected_range)
    magnitude = abs(float(value))
    return magnitude >= low if high is None else low <= magnitude < high


def derive_year(item: dict[str, Any]) -> int | None:
    year = item.get("year")
    if year not in (None, "", 0):
        try:
            return int(year)
        except (TypeError, ValueError):
            pass
    subject = item.get("subject") if isinstance(item.get("subject"), dict) else {}
    for value in (
        item.get("release_date"), item.get("bangumi_date"), item.get("date"),
        subject.get("date"),
    ):
        match = re.search(r"(?:19|20)\d{2}", str(value or ""))
        if match:
            return int(match.group())
    return None


def derive_year_quarter(item: dict[str, Any]) -> tuple[int | None, int | None]:
    """Return the stored year and a release-date quarter when it is knowable.

    A manually stored year remains authoritative.  We only attach a quarter
    from a date whose year agrees with that value, so inconsistent historical
    records are shown as ``季度未知`` instead of being silently misclassified.
    """
    year = derive_year(item)
    subject = item.get("subject") if isinstance(item.get("subject"), dict) else {}
    for value in (
        item.get("release_date"), item.get("bangumi_date"), item.get("date"),
        subject.get("date"),
    ):
        text = str(value or "").strip()
        match = re.search(r"((?:19|20)\d{2})\D*(0?[1-9]|1[0-2])(?:\D|$)", text)
        if not match:
            continue
        date_year, month = int(match.group(1)), int(match.group(2))
        if year is None:
            year = date_year
        if date_year == year:
            return year, ((month - 1) // 3) + 1
    return year, None


def year_quarter_label(item: dict[str, Any]) -> str:
    year, quarter = derive_year_quarter(item)
    if year is None:
        return "年份未知"
    if quarter is None:
        return f"{year}年 · 季度未知"
    return f"{year}年 · Q{quarter}"


def year_quarter_options(items: Iterable[dict[str, Any]]) -> list[str]:
    periods = {derive_year_quarter(item) for item in items}
    known = [period for period in periods if period[0] is not None]
    known.sort(key=lambda period: (int(period[0] or 0), int(period[1] or 0)), reverse=True)
    labels = [
        f"{year}年 · Q{quarter}" if quarter is not None else f"{year}年 · 季度未知"
        for year, quarter in known
    ]
    if (None, None) in periods:
        labels.append("年份未知")
    return labels


def year_quarter_matches(item: dict[str, Any], selected: str) -> bool:
    return selected == "全部" or year_quarter_label(item) == selected


def release_year_options(items: Iterable[dict[str, Any]]) -> list[int]:
    """Return all known release years, newest first."""
    return sorted(
        {year for item in items if (year := derive_year(item)) is not None},
        reverse=True,
    )


def release_period_scope(
    items: Iterable[dict[str, Any]], mode: str, *, year: int | None = None,
    quarter: int | None = None, start_year: int | None = None,
    end_year: int | None = None, years: Iterable[int] = (),
) -> list[dict[str, Any]]:
    """Select the exact release period used by the library/compare averages.

    Quarter statistics require a knowable release quarter.  Year-only legacy
    rows still participate in annual and continuous-range statistics, but are
    never silently assigned to a quarter. ``years`` remains as a compatibility
    input and is interpreted as the inclusive range between its oldest and
    newest values, never as a set of disconnected years.
    """
    rows = [dict(item) for item in items]
    if mode == "单季度":
        if year is None or quarter not in {1, 2, 3, 4}:
            return []
        return [item for item in rows if derive_year_quarter(item) == (int(year), int(quarter))]
    if mode == "单年":
        if year is None:
            return []
        return [item for item in rows if derive_year(item) == int(year)]
    if mode in {"年代范围", "多年合并"}:
        bounds = [int(value) for value in (start_year, end_year) if value is not None]
        if not bounds:
            bounds = [int(value) for value in years]
        if not bounds:
            return []
        first_year, last_year = min(bounds), max(bounds)
        return [
            item for item in rows
            if (item_year := derive_year(item)) is not None
            and first_year <= item_year <= last_year
        ]
    raise ValueError(f"未知时间均分模式：{mode}")


def release_period_scope_label(
    mode: str, *, year: int | None = None, quarter: int | None = None,
    start_year: int | None = None, end_year: int | None = None,
    years: Iterable[int] = (),
) -> str:
    """Build the compact, user-facing label for a period-average scope."""
    if mode == "单季度" and year is not None and quarter in {1, 2, 3, 4}:
        return f"{int(year)}年 · Q{int(quarter)}"
    if mode == "单年" and year is not None:
        return f"{int(year)}年"
    if mode in {"年代范围", "多年合并"}:
        bounds = [int(value) for value in (start_year, end_year) if value is not None]
        if not bounds:
            bounds = [int(value) for value in years]
        if not bounds:
            return "尚未选择年代范围"
        first_year, last_year = min(bounds), max(bounds)
        return f"{first_year}年" if first_year == last_year else f"{first_year}—{last_year}年"
    return "时间范围未定"


def item_tags(item: dict[str, Any]) -> set[str]:
    tags = item.get("tags")
    if not isinstance(tags, list) and isinstance(item.get("subject"), dict):
        tags = item["subject"].get("tags")
    if isinstance(tags, list):
        values = {str(tag.get("name") if isinstance(tag, dict) else tag).strip() for tag in tags}
        return {value for value in values if value}
    return {part.strip() for part in str(item.get("tag_names") or "").split("·") if part.strip()}


def matches_any_tag(item: dict[str, Any], selected_tags: Iterable[str]) -> bool:
    selected = {str(tag).strip() for tag in selected_tags if str(tag).strip()}
    return not selected or bool(item_tags(item) & selected)


def sort_null_last(
    items: Iterable[dict[str, Any]], field_name: str, descending: bool = True,
    *, absolute: bool = False,
) -> list[dict[str, Any]]:
    present, missing = [], []
    for item in items:
        value = item.get(field_name)
        (missing if value is None or value == "" else present).append(item)

    def key(item: dict[str, Any]) -> Any:
        value = item[field_name]
        if absolute:
            return abs(float(value))
        return value

    return sorted(present, key=key, reverse=descending) + missing


def average_non_null(items: Iterable[dict[str, Any]], field_name: str) -> float | None:
    values = [float(item[field_name]) for item in items if item.get(field_name) is not None]
    return round(sum(values) / len(values), 2) if values else None
