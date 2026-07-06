"""Shared score, difference, tag, year and null-safe sorting helpers."""
from __future__ import annotations

import re
from typing import Any, Iterable


SCORE_RANGES = [f"{value / 2:.1f} 到 {value / 2 + 0.5:.1f}" for value in range(1, 18)] + ["9.0 分以上"]
DIFF_ABS_RANGES = [
    "0 到 0.5", "0.5 到 1.0", "1.0 到 1.5", "1.5 到 2.0",
    "2.0 到 2.5", "2.5 到 3.0", "3.0 以上",
]
DIFF_DIRECTIONS = ["全部", "我高于 Bangumi", "我低于 Bangumi", "基本一致"]


def get_score_ranges(include_all: bool = False) -> list[str]:
    ranges = list(SCORE_RANGES)
    return (["全部"] + ranges) if include_all else ranges


def calculate_score_diff(item: dict[str, Any]) -> float | None:
    mine, public = item.get("score_total"), item.get("bangumi_score")
    if mine is None or public is None:
        return None
    return round(float(mine) - float(public), 1)


def format_diff(value: Any) -> str:
    return "—" if value is None else f"{float(value):+.1f}"


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
    match = re.search(r"(?:19|20)\d{2}", str(item.get("release_date") or ""))
    return int(match.group()) if match else None


def item_tags(item: dict[str, Any]) -> set[str]:
    tags = item.get("tags")
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
    return round(sum(values) / len(values), 1) if values else None
