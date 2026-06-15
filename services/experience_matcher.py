"""Experience-library matching and voting for the experience committee."""

from __future__ import annotations

import json
import math
import re
from typing import Any

from services.experience_library_loader import (
    check_experience_library_available,
    get_default_experience_library_path,
    load_experience_level_records,
)
from services.symbol_profile_engine import build_symbol_profile


STATE_VECTOR_KEYS = (
    "trend_direction",
    "trend_strength",
    "trend_quality_score",
    "capital_score",
    "capital_direction",
    "structure_score",
    "behavior_score",
    "risk_score",
    "risk_safe_score",
    "demand_score",
    "buy_demand_score",
    "sell_supply_score",
    "net_demand_score",
    "urgency_score",
    "sustainability_score",
    "trap_risk_score",
    "confidence",
    "data_integrity_score",
)
SIMILARITY_WEIGHTS = {
    "demand_score": 0.20,
    "trend_quality_score": 0.15,
    "capital_score": 0.15,
    "behavior_score": 0.15,
    "structure_score": 0.10,
    "risk_score": 0.15,
    "trap_risk_score": 0.10,
}
METRIC_FIELDS = (
    "historical_30m_up_probability",
    "historical_30m_down_probability",
    "historical_30m_sideways_probability",
    "historical_60m_up_probability",
    "historical_60m_down_probability",
    "historical_60m_sideways_probability",
    "avg_return_30m",
    "avg_return_60m",
    "median_return_30m",
    "median_return_60m",
    "mfe_p50",
    "mfe_p75",
    "mfe_p90",
    "mae_p50",
    "mae_p75",
    "mae_p90",
    "suggested_stop_loss",
    "suggested_take_profit_1",
    "suggested_take_profit_2",
    "suggested_trailing_stop",
    "historical_loss_probability",
    "trap_risk_avg",
)
PROBABILITY_MAP = {
    "historical_30m_up_probability": "future_30m_up_probability",
    "historical_30m_down_probability": "future_30m_down_probability",
    "historical_30m_sideways_probability": "future_30m_sideways_probability",
    "historical_60m_up_probability": "future_60m_up_probability",
    "historical_60m_down_probability": "future_60m_down_probability",
    "historical_60m_sideways_probability": "future_60m_sideways_probability",
}


def _to_float(value: Any, default: float = 0.0) -> float:
    try:
        if value is None:
            return default
        number = float(value)
        if math.isnan(number) or math.isinf(number):
            return default
        return number
    except (TypeError, ValueError):
        return default


def _clamp(value: Any, default: float = 0.0) -> float:
    return max(0.0, min(100.0, _to_float(value, default)))


def _probability_percent(value: Any) -> float:
    number = _to_float(value, 0.0)
    if abs(number) <= 1.0:
        number *= 100.0
    return round(max(0.0, min(100.0, number)), 2)


def _safe_vector(market_cognition: dict[str, Any]) -> dict[str, Any]:
    vector = market_cognition.get("state_vector") if isinstance(market_cognition.get("state_vector"), dict) else {}
    result: dict[str, Any] = {}
    for key in STATE_VECTOR_KEYS:
        if key in vector:
            result[key] = vector.get(key)
        elif key in market_cognition:
            result[key] = market_cognition.get(key)
    return result


def summarize_state_vector(state_vector: dict[str, Any]) -> str:
    if not state_vector:
        return "状态向量缺失"
    parts = [
        f"趋势{state_vector.get('trend_direction', '-')}/{state_vector.get('trend_strength', '-')}",
        f"趋势质量{state_vector.get('trend_quality_score', '-')}",
        f"资金{state_vector.get('capital_score', '-')}",
        f"结构{state_vector.get('structure_score', '-')}",
        f"行为{state_vector.get('behavior_score', '-')}",
        f"风险{state_vector.get('risk_score', '-')}",
        f"需求{state_vector.get('demand_score', '-')}",
        f"净需求{state_vector.get('net_demand_score', '-')}",
    ]
    return "，".join(parts)


def _factory_group_candidates(symbol: str, profile: dict[str, Any]) -> list[str]:
    profile_candidates = list(profile.get("experience_symbol_group_candidates") or [])
    if profile_candidates:
        unique = [str(item) for index, item in enumerate(profile_candidates) if item and item not in profile_candidates[:index]]
        non_unknown = [item for item in unique if item.upper() != "UNKNOWN"]
        unknown = [item for item in unique if item.upper() == "UNKNOWN"]
        return non_unknown + unknown
    symbol = str(symbol or "").upper()
    primary = str(profile.get("symbol_group") or "").strip()
    candidates: list[str] = []
    if symbol in {"BTCUSDT", "ETHUSDT"}:
        candidates.append("majors")
    elif symbol in {"BNBUSDT", "SOLUSDT", "XRPUSDT"}:
        candidates.append("large_alt")
    elif primary in {"MAJOR_HIGH_LIQUIDITY"}:
        candidates.extend(["majors", "large_alt"])
    elif primary in {"HIGH_VOLUME_ALT", "MID_VOLUME_ALT", "MEME_OR_HYPE", "LOW_LIQUIDITY_HIGH_VOL", "UNKNOWN"}:
        candidates.append("large_alt")
    candidates.append(primary)
    unique = [item for index, item in enumerate(candidates) if item and item not in candidates[:index]]
    non_unknown = [item for item in unique if str(item).upper() != "UNKNOWN"]
    unknown = [item for item in unique if str(item).upper() == "UNKNOWN"]
    return non_unknown + unknown


def build_experience_query_from_cognition(
    symbol: str,
    market_cognition: dict[str, Any],
    ticker: dict[str, Any] | None = None,
) -> dict[str, Any]:
    cognition = market_cognition or {}
    profile = build_symbol_profile(symbol, ticker=ticker)
    state_vector = _safe_vector(cognition)
    clean_symbol = str(symbol or cognition.get("symbol") or "").upper()
    group_candidates = _factory_group_candidates(clean_symbol, profile)
    primary_group = next((item for item in group_candidates if str(item).upper() != "UNKNOWN"), group_candidates[0] if group_candidates else profile.get("symbol_group", "UNKNOWN"))
    cognition_state_code = cognition.get("state_code")
    return {
        "symbol": clean_symbol,
        "symbol_group": primary_group,
        "primary_group": primary_group,
        "symbol_group_candidates": group_candidates,
        "symbol_profile": profile,
        "market_cognition_state_code": cognition_state_code,
        "experience_query_state_code": cognition_state_code,
        "state_code": cognition_state_code,
        "state_vector": state_vector,
        "state_vector_summary": summarize_state_vector(state_vector),
        "versions": {
            "schema_version": cognition.get("schema_version"),
            "state_language_version": cognition.get("state_language_version"),
            "cognition_model_version": cognition.get("cognition_model_version"),
            "weight_config_version": cognition.get("weight_config_version"),
            "similarity_config_version": cognition.get("similarity_config_version"),
        },
    }


def _empty_level(scope_type: str, reason: str = "未命中该层经验。") -> dict[str, Any]:
    return {
        "layer": scope_type,
        "scope_type": scope_type,
        "available": False,
        "matched": False,
        "match_type": "NONE",
        "matched_state_code": "",
        "matched_sample_count": 0,
        "sample_count": 0,
        "confidence": 0,
        "data_quality": 0,
        "similarity": 0,
        "weight": 0,
        "reason": reason,
        "warnings": [],
        "top_matches": [],
    }


def _parse_center(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    if isinstance(value, str) and value.strip():
        try:
            raw = json.loads(value)
            return raw if isinstance(raw, dict) else {}
        except Exception:
            return {}
    return {}


def _vector_similarity(query_vector: dict[str, Any], center_value: Any, fallback: Any) -> tuple[float, str]:
    center = _parse_center(center_value)
    if not center:
        return _clamp(_probability_percent(fallback) if _to_float(fallback, 0) <= 1 else fallback, 60), "state_vector_center 缺失，使用 avg_similarity 或默认相似度。"
    total_weight = 0.0
    weighted_distance = 0.0
    for key, weight in SIMILARITY_WEIGHTS.items():
        if key not in query_vector or key not in center:
            continue
        weighted_distance += abs(_to_float(query_vector.get(key), 0) - _to_float(center.get(key), 0)) * weight
        total_weight += weight
    if total_weight <= 0:
        return _clamp(_probability_percent(fallback) if _to_float(fallback, 0) <= 1 else fallback, 60), "状态向量可比字段不足，使用 avg_similarity 或默认相似度。"
    return round(max(0.0, 100.0 - weighted_distance / total_weight), 2), ""


def _state_code_distance(current: Any, candidate: Any) -> float:
    weights = {"T": 1.5, "C": 1.0, "S": 1.0, "B": 1.0, "R": 2.0, "D": 2.0}
    current_parts = {key: int(value) for key, value in re.findall(r"([TCSBRD])\s*(\d+)", str(current or "").upper())}
    candidate_parts = {key: int(value) for key, value in re.findall(r"([TCSBRD])\s*(\d+)", str(candidate or "").upper())}
    if current_parts and candidate_parts:
        return float(sum(abs(current_parts.get(key, 0) - candidate_parts.get(key, 0)) * weight for key, weight in weights.items()))
    current_numbers = [int(x) for x in re.findall(r"\d+", str(current or ""))]
    candidate_numbers = [int(x) for x in re.findall(r"\d+", str(candidate or ""))]
    if not current_numbers or not candidate_numbers:
        return 999.0
    size = max(len(current_numbers), len(candidate_numbers))
    current_numbers.extend([0] * (size - len(current_numbers)))
    candidate_numbers.extend([0] * (size - len(candidate_numbers)))
    positional_weights = [1.5, 1.0, 1.0, 1.0, 2.0, 2.0]
    positional_weights.extend([1.0] * max(0, size - len(positional_weights)))
    return float(sum(abs(a - b) * positional_weights[index] for index, (a, b) in enumerate(zip(current_numbers, candidate_numbers))))


def _best_state_code_distance(rows: list[dict[str, Any]], state_code: Any) -> float:
    distances = [_state_code_distance(state_code, row.get("state_code")) for row in rows if row.get("state_code") is not None]
    return min(distances) if distances else 999.0


def _select_similar_state_rows(rows: list[dict[str, Any]], state_code: Any, top_k: int) -> list[dict[str, Any]]:
    best_distance = _best_state_code_distance(rows, state_code)
    if best_distance >= 999.0:
        return []
    window = max(best_distance, 0.0) + 0.0001
    candidates = [row for row in rows if _state_code_distance(state_code, row.get("state_code")) <= window]
    return sorted(candidates, key=lambda row: (_state_code_distance(state_code, row.get("state_code")), -_to_float(row.get("sample_count"), 0)))[: max(top_k * 3, 10)]


def _select_vector_nearest_rows(rows: list[dict[str, Any]], query_vector: dict[str, Any], top_k: int) -> tuple[list[dict[str, Any]], list[str]]:
    warnings: list[str] = []
    enriched: list[dict[str, Any]] = []
    for row in rows:
        similarity, warning = _vector_similarity(query_vector, row.get("state_vector_center"), row.get("avg_similarity", 60))
        if warning and warning not in warnings:
            warnings.append(warning)
        enriched.append({**row, "_pre_similarity": similarity})
    enriched.sort(key=lambda row: (_to_float(row.get("_pre_similarity"), 0), _to_float(row.get("sample_count"), 0)), reverse=True)
    return enriched[: max(top_k * 3, 10)], warnings


def _scope_matches(row: dict[str, Any], expected: set[str]) -> bool:
    return str(row.get("scope_type") or "").strip().lower() in expected


def _row_metric(row: dict[str, Any], field: str) -> float:
    source = PROBABILITY_MAP.get(field, field)
    value = row.get(source)
    if field.endswith("_probability"):
        return _probability_percent(value)
    return _to_float(value, 0.0)


def _aggregate_rows(rows: list[dict[str, Any]], query_vector: dict[str, Any], state_code: Any, top_k: int) -> tuple[dict[str, Any], list[str]]:
    warnings: list[str] = []
    enriched: list[dict[str, Any]] = []
    for row in rows:
        similarity, warning = _vector_similarity(query_vector, row.get("state_vector_center"), row.get("avg_similarity", 60))
        if warning and warning not in warnings:
            warnings.append(warning)
        sample_count = max(0.0, _to_float(row.get("sample_count"), 0))
        confidence = _clamp(row.get("confidence"), 0)
        data_quality = _clamp(row.get("data_quality"), 50)
        state_distance = 0.0 if str(row.get("state_code")) == str(state_code) else _state_code_distance(state_code, row.get("state_code"))
        rank_score = similarity * 2 + min(sample_count, 1000) / 20 + confidence - state_distance * 15
        enriched.append({**row, "_similarity": similarity, "_state_distance": state_distance, "_rank_score": rank_score})
    enriched.sort(key=lambda item: (_to_float(item.get("_rank_score"), 0), _to_float(item.get("sample_count"), 0)), reverse=True)
    selected = enriched[: max(1, top_k)]
    total_basis = sum(max(1.0, _to_float(row.get("sample_count"), 0)) * max(1.0, _to_float(row.get("_similarity"), 0)) for row in selected)
    def weighted(field: str) -> float:
        if total_basis <= 0:
            return 0.0
        return sum(_row_metric(row, field) * max(1.0, _to_float(row.get("sample_count"), 0)) * max(1.0, _to_float(row.get("_similarity"), 0)) for row in selected) / total_basis

    sample_count = int(sum(max(0.0, _to_float(row.get("sample_count"), 0)) for row in selected))
    metrics = {field: round(weighted(field), 6) for field in METRIC_FIELDS}
    confidence = weighted("confidence") if "confidence" in METRIC_FIELDS else 0
    confidence = sum(_clamp(row.get("confidence"), 0) * max(1.0, _to_float(row.get("sample_count"), 0)) for row in selected) / max(sum(max(1.0, _to_float(row.get("sample_count"), 0)) for row in selected), 1)
    data_quality = sum(_clamp(row.get("data_quality"), 50) * max(1.0, _to_float(row.get("sample_count"), 0)) for row in selected) / max(sum(max(1.0, _to_float(row.get("sample_count"), 0)) for row in selected), 1)
    similarity = sum(_to_float(row.get("_similarity"), 0) * max(1.0, _to_float(row.get("sample_count"), 0)) for row in selected) / max(sum(max(1.0, _to_float(row.get("sample_count"), 0)) for row in selected), 1)
    aggregate = {
        **metrics,
        "matched_sample_count": sample_count,
        "sample_count": sample_count,
        "confidence": round(confidence, 2),
        "data_quality": round(data_quality, 2),
        "similarity": round(similarity, 2),
        "exact_state_code": any(str(row.get("state_code")) == str(state_code) for row in selected),
        "matched_state_code": str(selected[0].get("state_code") or "") if selected else "",
        "top_matches": [
            {
                "symbol": row.get("symbol"),
                "symbol_group": row.get("symbol_group"),
                "state_code": row.get("state_code"),
                "sample_count": int(_to_float(row.get("sample_count"), 0)),
                "similarity": row.get("_similarity"),
                "confidence": row.get("confidence"),
                "data_quality": row.get("data_quality"),
            }
            for row in selected[:5]
        ],
    }
    return aggregate, warnings


def _read_candidate_rows(
    level: str,
    experience_library_path: str | None,
    exact_filters: list[tuple[str, str, Any]],
    broad_filters: list[tuple[str, str, Any]],
) -> tuple[list[dict[str, Any]], list[str], list[str], bool]:
    exact = load_experience_level_records(level, experience_library_path, filters=exact_filters)
    warnings = list(exact.get("warnings") or [])
    errors = list(exact.get("errors") or [])
    rows = list(exact.get("records") or [])
    if rows:
        return rows, warnings, errors, True
    broad = load_experience_level_records(level, experience_library_path, filters=broad_filters)
    warnings.extend(str(item) for item in list(broad.get("warnings") or []) if item not in warnings)
    errors.extend(str(item) for item in list(broad.get("errors") or []) if item not in errors)
    return list(broad.get("records") or []), warnings, errors, False


def _match_level(level: str, query: dict[str, Any], experience_library_path: str | None, top_k: int) -> dict[str, Any]:
    symbol = str(query.get("symbol") or "").upper()
    state_code = query.get("state_code")
    groups = [str(x) for x in list(query.get("symbol_group_candidates") or [query.get("symbol_group")]) if x]
    query_vector = query.get("state_vector") if isinstance(query.get("state_vector"), dict) else {}
    if level == "symbol_level":
        scope_type = "SYMBOL"
        rows, warnings, errors, exact = _read_candidate_rows(
            level,
            experience_library_path,
            [("symbol", "==", symbol), ("state_code", "==", state_code)],
            [("symbol", "==", symbol)],
        )
        rows = [row for row in rows if _scope_matches(row, {"symbol"}) and str(row.get("symbol") or "").upper() == symbol]
        used_group = ""
    elif level == "group_level":
        scope_type = "GROUP"
        rows = []
        used_group = ""
        warnings = []
        errors = []
        exact = False
        for group in groups:
            found, level_warnings, level_errors, level_exact = _read_candidate_rows(
                level,
                experience_library_path,
                [("symbol_group", "==", group), ("state_code", "==", state_code)],
                [("symbol_group", "==", group)],
            )
            warnings.extend(item for item in level_warnings if item not in warnings)
            errors.extend(item for item in level_errors if item not in errors)
            rows = [row for row in found if _scope_matches(row, {"group", "symbol_group"}) and str(row.get("symbol_group") or "") == group]
            exact = level_exact
            if rows:
                used_group = group
                break
    else:
        scope_type = "GLOBAL"
        used_group = ""
        rows, warnings, errors, exact = _read_candidate_rows(
            level,
            experience_library_path,
            [("state_code", "==", state_code)],
            [],
        )
        rows = [row for row in rows if _scope_matches(row, {"global"})]

    if errors and not rows:
        return {**_empty_level(scope_type, "该层经验读取失败。"), "available": False, "warnings": warnings, "errors": errors}
    if not rows:
        return {**_empty_level(scope_type), "available": not errors, "warnings": warnings, "errors": errors}
    match_type = "EXACT_STATE_CODE"
    reason = "命中 exact state_code。"
    if not exact:
        similar_rows = _select_similar_state_rows(rows, state_code, top_k)
        if similar_rows:
            rows = similar_rows
            match_type = "SIMILAR_STATE_CODE"
            reason = "未命中 exact state_code，使用相似 state_code fallback。"
            warnings.append("exact state_code 未命中，已按相似 state_code fallback。")
        else:
            rows, vector_warnings = _select_vector_nearest_rows(rows, query_vector, top_k)
            warnings.extend(item for item in vector_warnings if item not in warnings)
            match_type = "VECTOR_NEAREST"
            reason = "未命中 exact 与相似 state_code，使用 state_vector 最近邻 fallback。"
            warnings.append("exact state_code 未命中，已按 state_vector 最近邻 fallback。")
    aggregate, sim_warnings = _aggregate_rows(rows, query_vector, state_code, top_k)
    return {
        **_empty_level(scope_type),
        **aggregate,
        "available": True,
        "matched": True,
        "layer": scope_type,
        "match_type": match_type,
        "used_symbol_group": used_group,
        "candidate_groups": groups,
        "reason": reason,
        "warnings": warnings + [item for item in sim_warnings if item not in warnings],
        "errors": errors,
    }


def _base_layer_weights(symbol_sample_count: int) -> dict[str, float]:
    if symbol_sample_count >= 300:
        return {"symbol_level": 0.60, "group_level": 0.25, "global_level": 0.15}
    if symbol_sample_count >= 100:
        return {"symbol_level": 0.35, "group_level": 0.45, "global_level": 0.20}
    return {"symbol_level": 0.10, "group_level": 0.60, "global_level": 0.30}


def _effective_weights(levels: dict[str, dict[str, Any]]) -> dict[str, float]:
    base = _base_layer_weights(int(_to_float(levels["symbol_level"].get("matched_sample_count"), 0)))
    adjusted: dict[str, float] = {}
    for key, level in levels.items():
        if not level.get("matched"):
            adjusted[key] = 0.0
            continue
        quality_factor = max(0.05, _clamp(level.get("confidence"), 0) / 100 * _clamp(level.get("data_quality"), 50) / 100 * _clamp(level.get("similarity"), 60) / 100)
        adjusted[key] = base.get(key, 0.0) * quality_factor
    total = sum(adjusted.values())
    if total <= 0:
        matched = [key for key, level in levels.items() if level.get("matched")]
        return {key: (1 / len(matched) if key in matched and matched else 0.0) for key in levels}
    return {key: round(value / total, 6) for key, value in adjusted.items()}


def _blend(levels: dict[str, dict[str, Any]], weights: dict[str, float]) -> dict[str, Any]:
    result = {field: 0.0 for field in METRIC_FIELDS}
    for field in METRIC_FIELDS:
        result[field] = round(sum(_to_float(levels[key].get(field), 0) * weights.get(key, 0) for key in levels), 6)
    matched_layers = [levels[key].get("scope_type") for key in levels if levels[key].get("matched")]
    result.update(
        {
            "matched": bool(matched_layers),
            "matched_layers": matched_layers,
            "matched_sample_count": int(sum(_to_float(level.get("matched_sample_count"), 0) for level in levels.values() if level.get("matched"))),
            "experience_confidence": round(sum(_clamp(levels[key].get("confidence"), 0) * weights.get(key, 0) for key in levels), 2),
            "experience_data_quality": round(sum(_clamp(levels[key].get("data_quality"), 0) * weights.get(key, 0) for key in levels), 2),
            "layer_weights": weights,
        }
    )
    return result


def _sample_confidence_policy(sample_count: int, matched_layers: list[Any]) -> dict[str, Any]:
    has_symbol = "SYMBOL" in {str(layer) for layer in matched_layers}
    if sample_count < 30:
        level = "样本严重不足"
        cap = 20.0
        participation = "弃权"
        note = "匹配样本不足 30 条，不足以形成稳定历史经验，经验委员弃权，仅展示历史参考。"
    elif sample_count < 100:
        level = "样本偏少"
        cap = 40.0
        participation = "仅参考"
        note = "匹配样本少于 100 条，历史结果只能作为弱参考，不允许强 SUPPORT。"
    elif sample_count < 300:
        level = "样本中等"
        cap = 65.0
        participation = "谨慎参与"
        note = "匹配样本达到中等规模，经验委员可谨慎参与投票。"
    else:
        level = "样本充足"
        cap = 100.0
        participation = "参与投票"
        note = "匹配样本较充足，经验委员根据历史概率、MFE/MAE 和回撤分布参与投票。"
    if matched_layers and not has_symbol:
        cap = min(cap, 65.0)
        if participation == "参与投票":
            participation = "谨慎参与"
        note = f"当前币种暂无单币种历史经验，系统使用同类币种与全市场经验作为参考。{note}"
    return {
        "sample_confidence_level": level,
        "experience_confidence_cap": cap,
        "experience_participation_status": participation,
        "sample_confidence_note": note,
        "single_symbol_experience_missing": bool(matched_layers and not has_symbol),
    }


def _vote_from_blended(blended: dict[str, Any], query: dict[str, Any]) -> dict[str, Any]:
    if not blended.get("matched"):
        current_integrity = _clamp((query.get("state_vector") or {}).get("data_integrity_score"), 0)
        return {
            "vote": "ABSTAIN",
            "direction": "WAIT",
            "score": 0,
            "confidence": 0,
            "data_integrity_score": current_integrity,
            "current_data_integrity_score": current_integrity,
            "sample_confidence_level": "无匹配样本",
            "experience_participation_status": "弃权",
            "abstain_reason": "三层经验均未命中。",
            "reason": "三层经验均未命中，经验委员弃权。",
        }
    up30 = _to_float(blended.get("historical_30m_up_probability"), 0)
    down30 = _to_float(blended.get("historical_30m_down_probability"), 0)
    avg30 = _to_float(blended.get("avg_return_30m"), 0)
    mfe90 = abs(_to_float(blended.get("mfe_p90"), 0))
    mae90 = abs(_to_float(blended.get("mae_p90"), 0))
    loss_probability = _to_float(blended.get("historical_loss_probability"), 0)
    trap_risk = _to_float(blended.get("trap_risk_avg"), _to_float((query.get("state_vector") or {}).get("trap_risk_score"), 0))
    matched_layers = list(blended.get("matched_layers") or [])
    sample_count = int(_to_float(blended.get("matched_sample_count"), 0))
    sample_policy = _sample_confidence_policy(sample_count, matched_layers)
    raw_experience_confidence = _clamp(blended.get("experience_confidence"), 0)
    if sample_policy.get("single_symbol_experience_missing"):
        raw_experience_confidence *= 0.85
    confidence = min(raw_experience_confidence, _to_float(sample_policy.get("experience_confidence_cap"), 100))
    data_quality = _clamp(blended.get("experience_data_quality"), 0)
    current_integrity = _clamp((query.get("state_vector") or {}).get("data_integrity_score"), data_quality)
    sample_score = min(20.0, math.log10(max(sample_count, 1)) * 8)
    prob_edge = up30 - down30
    return_score = max(-15.0, min(15.0, avg30 * 5000))
    rr_score = max(0.0, min(20.0, (mfe90 / max(mae90, 0.0001)) * 8))
    score = _clamp(50 + prob_edge * 0.45 + return_score + rr_score + sample_score * 0.5 + data_quality * 0.10 - trap_risk * 0.18 - loss_probability * 0.22)
    abstain_reason = ""
    if sample_count < 30:
        vote = "ABSTAIN"
        direction = "WAIT"
        abstain_reason = "样本数不足 30，经验委员弃权，仅展示历史参考。"
    elif confidence < 30:
        vote = "ABSTAIN"
        direction = "WAIT"
        abstain_reason = "历史经验置信度低于 30，经验委员弃权，仅展示历史参考。"
    elif data_quality < 35 or mae90 >= 0.08 or loss_probability >= 70:
        vote = "VETO" if mae90 >= 0.12 or data_quality < 20 else "OPPOSE"
        direction = "WAIT"
    elif down30 >= 60:
        vote = "OPPOSE"
        direction = "SHORT"
    elif up30 >= 60 and avg30 > 0 and mae90 <= max(0.06, mfe90 * 2.2):
        vote = "SUPPORT" if score >= 70 and confidence >= 50 else "CAUTIOUS_SUPPORT"
        direction = "LONG"
    elif up30 >= 55:
        vote = "CAUTIOUS_SUPPORT"
        direction = "LONG"
    else:
        vote = "ABSTAIN" if score < 45 else "CAUTIOUS_SUPPORT"
        direction = "WAIT" if vote == "ABSTAIN" else ("LONG" if up30 >= down30 else "SHORT")
        if vote == "ABSTAIN":
            abstain_reason = "历史概率优势和收益质量不足，经验委员弃权。"
    if sample_count < 100 and vote == "SUPPORT":
        vote = "CAUTIOUS_SUPPORT"
        sample_policy["experience_participation_status"] = "仅参考"
    layers = "、".join(str(item) for item in blended.get("matched_layers") or []) or "无"
    sample_note = str(sample_policy.get("sample_confidence_note") or "")
    abstain_text = f"{abstain_reason}" if abstain_reason else ""
    reason = (
        f"匹配层级：{layers}，总样本{int(_to_float(blended.get('matched_sample_count'), 0))}。"
        f"{sample_note}"
        f"30m上涨/震荡/下跌概率为{up30:.1f}%/{_to_float(blended.get('historical_30m_sideways_probability'), 0):.1f}%/{down30:.1f}%，"
        f"60m上涨/震荡/下跌概率为{_to_float(blended.get('historical_60m_up_probability'), 0):.1f}%/{_to_float(blended.get('historical_60m_sideways_probability'), 0):.1f}%/{_to_float(blended.get('historical_60m_down_probability'), 0):.1f}%。"
        f"MFE90约{mfe90 * 100:.2f}%，MAE90约{-mae90 * 100:.2f}%，平均30m收益{avg30 * 100:.2f}%。"
        f"经验评分{score:.1f}，ExperienceConfidence {confidence:.1f}，DataIntegrity {current_integrity:.1f}，经验数据质量{data_quality:.1f}，因此给出{vote}/{direction}。"
        f"{abstain_text}"
    )
    return {
        "vote": vote,
        "direction": direction,
        "score": round(score, 2),
        "confidence": round(confidence, 2),
        "raw_experience_confidence": round(raw_experience_confidence, 2),
        "data_integrity_score": round(current_integrity, 2),
        "current_data_integrity_score": round(current_integrity, 2),
        "experience_data_quality": round(data_quality, 2),
        "abstain_reason": abstain_reason,
        "reason": reason,
        **sample_policy,
    }


def match_experience(query: dict[str, Any], experience_library_path: str | None = None, top_k: int = 50) -> dict[str, Any]:
    status = check_experience_library_available(experience_library_path)
    if not status.get("available"):
        reason = "经验库未接入或不可读，当前经验委员弃权。"
        if status.get("warnings"):
            reason = f"{reason}；{'; '.join(str(item) for item in list(status.get('warnings') or [])[:3])}"
        return {
            "available": False,
            "matched": False,
            "vote": "ABSTAIN",
            "direction": "WAIT",
            "score": 0,
            "confidence": 0,
            "data_integrity_score": 0,
            "reason": reason,
            "experience_library_path": status.get("path") or get_default_experience_library_path(),
            "experience_library_status": status,
            "query": query,
            "symbol_level": _empty_level("SYMBOL", reason),
            "group_level": _empty_level("GROUP", reason),
            "global_level": _empty_level("GLOBAL", reason),
            "top_k": top_k,
        }

    levels = {
        "symbol_level": _match_level("symbol_level", query, experience_library_path, top_k),
        "group_level": _match_level("group_level", query, experience_library_path, top_k),
        "global_level": _match_level("global_level", query, experience_library_path, top_k),
    }
    weights = _effective_weights(levels)
    for key, weight in weights.items():
        levels[key]["weight"] = weight
    blended = _blend(levels, weights)
    vote = _vote_from_blended(blended, query)
    all_warnings = []
    for level in levels.values():
        all_warnings.extend(str(item) for item in list(level.get("warnings") or []) if item not in all_warnings)
    return {
        "available": True,
        **blended,
        **vote,
        "experience_library_path": status.get("path") or get_default_experience_library_path(),
        "experience_library_status": status,
        "query": query,
        "symbol_level": levels["symbol_level"],
        "group_level": levels["group_level"],
        "global_level": levels["global_level"],
        "warnings": all_warnings,
        "top_k": top_k,
    }
