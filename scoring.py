"""Central scoring rules shared by every Yang-gumi page."""
from __future__ import annotations

import copy
import re
import json
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parent
DATA_DIR = ROOT / "data"
SETTINGS_PATH = DATA_DIR / "scoring_settings.json"

FIELD_LABELS = {
    "score_story": "剧情",
    "score_character": "角色塑造",
    "score_art": "作画 / 摄影",
    "score_direction": "演出",
    "score_music": "音乐 / 配音",
    "score_pacing": "节奏",
    "score_personal": "个人偏爱",
    "rewatch_value": "重看 / 重玩价值",
    "score_aftertaste": "情绪后劲",
    "score_uniqueness": "独特性",
    "score_atmosphere": "氛围感",
    "score_influence": "影响力",
    "score_originality": "开创性",
}
CONFIGURABLE_SCORE_FIELDS = tuple(FIELD_LABELS)

SCORE_GROUPS = {
    "body": "作品本体",
    "feeling": "个人感受",
    "era": "时代加权",
}

DEFAULT_SCORE_CONFIG: dict[str, dict[str, Any]] = {
    "body": {
        "cap": 9.0,
        "weights": {
            "score_story": 0.40,
            "score_character": 0.15,
            "score_art": 0.20,
            "score_direction": 0.10,
            "score_music": 0.10,
            "score_pacing": 0.05,
        },
    },
    "feeling": {
        "cap": 0.7,
        "weights": {
            "score_personal": 0.40,
            "rewatch_value": 0.20,
            "score_aftertaste": 0.20,
            "score_uniqueness": 0.10,
            "score_atmosphere": 0.10,
        },
    },
    "era": {
        "cap": 0.3,
        "weights": {
            "score_influence": 0.60,
            "score_originality": 0.40,
        },
    },
}

MAIN_SCORE_WEIGHTS = copy.deepcopy(DEFAULT_SCORE_CONFIG["body"]["weights"])
BONUS_SCORE_WEIGHTS = copy.deepcopy(DEFAULT_SCORE_CONFIG["feeling"]["weights"])
SPECIAL_SCORE_WEIGHTS = copy.deepcopy(DEFAULT_SCORE_CONFIG["era"]["weights"])
COMPONENT_SCORE_FIELDS = tuple(MAIN_SCORE_WEIGHTS) + tuple(BONUS_SCORE_WEIGHTS) + tuple(SPECIAL_SCORE_WEIGHTS)
SPECIAL_VOTE_THRESHOLD = 3000
BONUS_SCORE_CAP = float(DEFAULT_SCORE_CONFIG["feeling"]["cap"])
SPECIAL_SCORE_CAP = float(DEFAULT_SCORE_CONFIG["era"]["cap"])
IMBALANCE_PENALTY_FIELD = "score_imbalance_penalty"
IMBALANCE_PENALTY_TIERS = ((3.5, 2.0), (3.0, 1.0), (2.5, 0.5))
IMBALANCE_PENALTY_MAX = 2.0


def _number(value: Any) -> float | None:
    if value in (None, ""):
        return None
    try:
        return max(0.0, min(10.0, float(value)))
    except (TypeError, ValueError):
        return None


def _positive(value: Any, fallback: float) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return fallback
    return parsed if parsed > 0 else fallback


def default_score_config() -> dict[str, dict[str, Any]]:
    return copy.deepcopy(DEFAULT_SCORE_CONFIG)


def _clean_label(value: Any, fallback: str) -> str:
    label = str(value or "").strip()
    return label[:24] if label else fallback


def _valid_field(field: Any) -> str | None:
    raw = str(field or "").strip()
    if raw in CONFIGURABLE_SCORE_FIELDS:
        return raw
    if re.fullmatch(r"custom_[a-z0-9_]{6,48}", raw):
        return raw
    return None


def _merge_score_config(current: Any) -> dict[str, dict[str, Any]]:
    merged = default_score_config()
    if not isinstance(current, dict):
        return merged
    for group_key, default_group in DEFAULT_SCORE_CONFIG.items():
        incoming_group = current.get(group_key)
        if not isinstance(incoming_group, dict):
            continue
        merged[group_key]["cap"] = _positive(incoming_group.get("cap"), float(default_group["cap"]))
        incoming_weights = incoming_group.get("weights")
        if isinstance(incoming_weights, dict):
            clean_weights: dict[str, float] = {}
            for field, weight in incoming_weights.items():
                clean_field = _valid_field(field)
                if clean_field:
                    clean_weights[clean_field] = _positive(weight, 0.10)
            if clean_weights:
                merged[group_key]["weights"] = clean_weights
        labels = dict(incoming_group.get("labels") or {})
        if labels:
            merged[group_key]["labels"] = {
                field: _clean_label(labels.get(field), FIELD_LABELS.get(field, field))
                for field in merged[group_key]["weights"]
            }
        else:
            merged[group_key]["labels"] = {
                field: FIELD_LABELS.get(field, field.replace("custom_", "自定义 "))
                for field in merged[group_key]["weights"]
            }
    return merged


def load_score_config() -> dict[str, dict[str, Any]]:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    if not SETTINGS_PATH.exists():
        return save_score_config(default_score_config())
    try:
        return _merge_score_config(json.loads(SETTINGS_PATH.read_text(encoding="utf-8")))
    except (OSError, ValueError, json.JSONDecodeError):
        return save_score_config(default_score_config())


def save_score_config(config: dict[str, Any]) -> dict[str, dict[str, Any]]:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    clean = _merge_score_config(config)
    temp = SETTINGS_PATH.with_suffix(".tmp")
    temp.write_text(json.dumps(clean, ensure_ascii=False, indent=2), encoding="utf-8")
    temp.replace(SETTINGS_PATH)
    return clean


def reset_score_config() -> dict[str, dict[str, Any]]:
    return save_score_config(default_score_config())


def score_weights(group_key: str, config: dict[str, Any] | None = None) -> dict[str, float]:
    active = _merge_score_config(config or load_score_config())
    return dict(active[group_key]["weights"])


def score_labels(group_key: str, config: dict[str, Any] | None = None) -> dict[str, str]:
    active = _merge_score_config(config or load_score_config())
    labels = active[group_key].get("labels") or {}
    return {field: _clean_label(labels.get(field), FIELD_LABELS.get(field, field)) for field in active[group_key]["weights"]}


def score_label(field: str, config: dict[str, Any] | None = None) -> str:
    active = _merge_score_config(config or load_score_config())
    for group in active.values():
        if field in group.get("weights", {}):
            return _clean_label((group.get("labels") or {}).get(field), FIELD_LABELS.get(field, field))
    return FIELD_LABELS.get(field, field)


def inactive_builtin_fields(config: dict[str, Any] | None = None) -> dict[str, str]:
    active = set(all_component_fields(config))
    return {field: FIELD_LABELS[field] for field in CONFIGURABLE_SCORE_FIELDS if field not in active}


def score_cap(group_key: str, config: dict[str, Any] | None = None) -> float:
    active = _merge_score_config(config or load_score_config())
    return float(active[group_key]["cap"])


def score_item_cap(group_key: str, field: str, config: dict[str, Any] | None = None) -> float:
    """Return the maximum contribution of one scoring item."""
    active = _merge_score_config(config or load_score_config())
    cap = float(active[group_key]["cap"])
    weight = float(active[group_key]["weights"].get(field, 0.0))
    return round(cap * weight, 4)


def all_component_fields(config: dict[str, Any] | None = None) -> tuple[str, ...]:
    active = _merge_score_config(config or load_score_config())
    return tuple(active["body"]["weights"]) + tuple(active["feeling"]["weights"]) + tuple(active["era"]["weights"])


def _custom_scores(data: dict[str, Any]) -> dict[str, Any]:
    raw = data.get("custom_scores")
    if isinstance(raw, dict):
        return raw
    raw = data.get("custom_scores_json")
    if not raw:
        return {}
    try:
        parsed = json.loads(raw) if isinstance(raw, str) else raw
    except (TypeError, json.JSONDecodeError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _field_value(data: dict[str, Any], field: str) -> Any:
    if field in data:
        return data.get(field)
    return _custom_scores(data).get(field)


def _normalized_weighted_score(data: dict[str, Any], weights: dict[str, float]) -> float | None:
    active = {field: value for field in weights if (value := _number(_field_value(data, field))) is not None}
    if not active:
        return None
    active_weight = sum(weights[field] for field in active)
    return sum(value * weights[field] for field, value in active.items()) / active_weight


def should_show_special_scores(bangumi_total_votes: Any) -> bool:
    try:
        return int(bangumi_total_votes) > SPECIAL_VOTE_THRESHOLD
    except (TypeError, ValueError):
        return False


def calculate_main_score(data: dict[str, Any], config: dict[str, Any] | None = None) -> float | None:
    score = _normalized_weighted_score(data, score_weights("body", config))
    cap = score_cap("body", config)
    return None if score is None else round(score / 10.0 * cap, 4)


def calculate_bonus_score(data: dict[str, Any], config: dict[str, Any] | None = None) -> float:
    score = _normalized_weighted_score(data, score_weights("feeling", config))
    cap = score_cap("feeling", config)
    return 0.0 if score is None else round(score / 10.0 * cap, 4)


def calculate_special_score(
    data: dict[str, Any],
    bangumi_total_votes: Any,
    config: dict[str, Any] | None = None,
) -> float | None:
    if not should_show_special_scores(bangumi_total_votes):
        return None
    weights = score_weights("era", config)
    filled = {field: value for field in weights if (value := _number(_field_value(data, field))) is not None}
    if not filled:
        return None
    cap = score_cap("era", config)
    score = cap * sum(value / 10.0 * weights[field] for field, value in filled.items())
    return round(score, 4)


def calculate_main_score_gap(data: dict[str, Any], config: dict[str, Any] | None = None) -> float | None:
    values = [_number(_field_value(data, field)) for field in score_weights("body", config)]
    filled = [value for value in values if value is not None]
    if len(filled) < 2:
        return None
    return round(max(filled) - min(filled), 4)


def calculate_pre_penalty_score(
    data: dict[str, Any],
    bangumi_total_votes: Any = None,
    config: dict[str, Any] | None = None,
) -> float | None:
    main = calculate_main_score(data, config)
    if main is None:
        return None
    bonus = calculate_bonus_score(data, config)
    special = calculate_special_score(data, bangumi_total_votes, config)
    return min(10.0, max(0.0, main + bonus + (special or 0.0)))


def imbalance_penalty_cap(
    data: dict[str, Any],
    bangumi_total_votes: Any = None,
    config: dict[str, Any] | None = None,
) -> float:
    gap = calculate_main_score_gap(data, config)
    if gap is None:
        return 0.0
    for threshold, cap in IMBALANCE_PENALTY_TIERS:
        if gap > threshold:
            return min(cap, IMBALANCE_PENALTY_MAX)
    return 0.0


def should_show_imbalance_penalty(data: dict[str, Any], bangumi_total_votes: Any = None) -> bool:
    return imbalance_penalty_cap(data, bangumi_total_votes) > 0.0


def calculate_imbalance_penalty(
    data: dict[str, Any],
    bangumi_total_votes: Any = None,
    config: dict[str, Any] | None = None,
) -> float | None:
    cap = imbalance_penalty_cap(data, bangumi_total_votes, config)
    if cap <= 0.0:
        return None
    penalty_score = _number(data.get(IMBALANCE_PENALTY_FIELD)) or 0.0
    return round(min(IMBALANCE_PENALTY_MAX, penalty_score / 10.0 * cap), 4)


def calculate_total_score(
    data: dict[str, Any],
    bangumi_total_votes: Any = None,
    auto_score: bool = True,
    manual_score: Any = None,
    config: dict[str, Any] | None = None,
) -> float | None:
    if not auto_score:
        value = _number(manual_score)
        return None if value is None else round(value, 2)
    main = calculate_main_score(data, config)
    if main is None:
        return None
    pre_penalty = calculate_pre_penalty_score(data, bangumi_total_votes, config) or 0.0
    penalty = calculate_imbalance_penalty(data, bangumi_total_votes, config) or 0.0
    return round(min(10.0, max(0.0, pre_penalty - penalty)), 2)


def explain_score_breakdown(data: dict[str, Any], config: dict[str, Any] | None = None) -> dict[str, float | None]:
    votes = data.get("bangumi_total_votes")
    main = calculate_main_score(data, config)
    bonus = calculate_bonus_score(data, config)
    special = calculate_special_score(data, votes, config)
    penalty = calculate_imbalance_penalty(data, votes, config)
    total = calculate_total_score(data, votes, config=config)
    return {
        "main_score": None if main is None else round(main, 2),
        "bonus_score": round(bonus, 2),
        "special_score": None if special is None else round(special, 2),
        "imbalance_gap": calculate_main_score_gap(data, config),
        "imbalance_penalty_cap": imbalance_penalty_cap(data, votes, config),
        "imbalance_penalty_score": _number(data.get(IMBALANCE_PENALTY_FIELD)),
        "imbalance_penalty": None if penalty is None else round(penalty, 2),
        "total_score": total,
    }


def calculate_composite_score(values: dict[str, Any]) -> float | None:
    """Compatibility wrapper for older callers."""
    return calculate_total_score(values, values.get("bangumi_total_votes"))


def default_auto_score(values: dict[str, Any]) -> bool:
    """Infer legacy manual totals without adding another database column."""
    calculated = calculate_composite_score(values)
    saved = values.get("score_total")
    if calculated is None:
        return saved is None
    return saved is None or abs(float(saved) - calculated) < 0.005
