"""Experience-library matching and voting for the experience committee."""

from __future__ import annotations

import math
from typing import Any

from services.experience_similarity import (
    state_code_distance,
    state_code_similarity,
    state_vector_similarity,
)
from services.experience_library_loader import (
    check_experience_library_available,
    get_default_experience_library_path,
    load_experience_level_records,
    resolve_experience_library_path,
)
from services.symbol_profile_engine import build_symbol_profile
from services.trading_cost_engine import round_trip_cost_rate


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
MATCH_TYPE_WEIGHTS = {
    "exact_state_code": 1.00,
    "similar_state_code": 0.70,
    "vector_nearest": 0.60,
    "group_exact": 0.75,
    "group_similar": 0.55,
    "global_exact": 0.45,
    "global_similar": 0.35,
}
SIMILARITY_MINIMUM = 70.0
SIMILARITY_PRIORITY = 80.0
VECTOR_LOW_SAMPLE_MINIMUM = 60.0
SIMILARITY_CUTOFF = 75.0
EDGE_THRESHOLD = 4.0
LAYER_ORDER = [
    "SYMBOL_EXACT",
    "SYMBOL_SIMILAR",
    "SYMBOL_VECTOR_NEAREST",
    "GROUP_EXACT",
    "GROUP_SIMILAR",
    "GROUP_VECTOR_NEAREST",
    "GLOBAL_EXACT",
    "GLOBAL_SIMILAR",
    "GLOBAL_VECTOR_NEAREST",
]
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
        return [str(item) for index, item in enumerate(profile_candidates) if item and item not in profile_candidates[:index]]
    symbol = str(symbol or "").upper()
    primary = str(profile.get("symbol_group") or "").strip()
    candidates: list[str] = [primary]
    if symbol in {"BTCUSDT", "ETHUSDT"}:
        candidates.append("majors")
    elif symbol in {"BNBUSDT", "SOLUSDT", "XRPUSDT"}:
        candidates.append("large_alt")
    elif primary in {"MAJOR_HIGH_LIQUIDITY"}:
        candidates.extend(["majors", "large_alt"])
    elif primary in {"HIGH_VOLUME_ALT", "MID_VOLUME_ALT", "MEME_OR_HYPE", "LOW_LIQUIDITY_HIGH_VOL", "UNKNOWN"}:
        candidates.append("large_alt")
    return [item for index, item in enumerate(candidates) if item and item not in candidates[:index]]


def _primary_group_from_candidates(candidates: list[str], fallback: Any = "UNKNOWN") -> str:
    return str(next((item for item in candidates if str(item).upper() != "UNKNOWN"), candidates[0] if candidates else fallback))


def _group_query_order(query: dict[str, Any]) -> list[str]:
    candidates = [str(x) for x in list(query.get("symbol_group_candidates") or [query.get("symbol_group")]) if x]
    primary = str(query.get("primary_group") or query.get("symbol_group") or "").strip()
    ordered = [primary] + candidates if primary else candidates
    return [item for index, item in enumerate(ordered) if item and item not in ordered[:index]]


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
    primary_group = _primary_group_from_candidates(group_candidates, profile.get("symbol_group", "UNKNOWN"))
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
        "exact_sample_count": 0,
        "similar_state_sample_count": 0,
        "vector_nearest_sample_count": 0,
        "total_matched_sample_count": 0,
        "avg_similarity": 0,
        "used_match_layers": [],
        "match_expansion_note": "",
        "confidence": 0,
        "data_quality": 0,
        "similarity": 0,
        "weight": 0,
        "reason": reason,
        "warnings": [],
        "top_matches": [],
    }


def _vector_similarity(query_vector: dict[str, Any], center_value: Any, fallback: Any) -> tuple[float, str]:
    result = state_vector_similarity(query_vector, center_value)
    if result.get("similarity_confidence", 0) <= 0:
        return _clamp(_probability_percent(fallback) if _to_float(fallback, 0) <= 1 else fallback, 60), "state_vector_center 缺失，使用 avg_similarity 或默认相似度。"
    return _clamp(result.get("similarity"), 0), str(result.get("warning") or "")


def _state_code_distance(current: Any, candidate: Any) -> float:
    return state_code_distance(current, candidate)


def _best_state_code_distance(rows: list[dict[str, Any]], state_code: Any) -> float:
    distances = [_state_code_distance(state_code, row.get("state_code")) for row in rows if row.get("state_code") is not None]
    return min(distances) if distances else 999.0


def _row_identity(row: dict[str, Any]) -> tuple[Any, Any, Any]:
    return (row.get("symbol"), row.get("symbol_group"), row.get("state_code"))


def _scope_match_key(scope_type: str, match_type: str) -> str:
    scope = str(scope_type or "").upper()
    match = str(match_type or "").lower()
    if scope == "SYMBOL":
        return match
    if scope == "GROUP":
        return "group_exact" if match == "exact_state_code" else "group_similar"
    return "global_exact" if match == "exact_state_code" else "global_similar"


def _layer_name(scope_type: str, match_type: str) -> str:
    scope = str(scope_type or "").upper()
    match = str(match_type or "").lower()
    if scope == "SYMBOL" and match == "exact_state_code":
        return "SYMBOL_EXACT"
    if scope == "SYMBOL" and match == "similar_state_code":
        return "SYMBOL_SIMILAR"
    if scope == "SYMBOL" and match == "vector_nearest":
        return "SYMBOL_VECTOR_NEAREST"
    if scope == "GROUP" and match == "exact_state_code":
        return "GROUP_EXACT"
    if scope == "GROUP" and match == "similar_state_code":
        return "GROUP_SIMILAR"
    if scope == "GROUP" and match == "vector_nearest":
        return "GROUP_VECTOR_NEAREST"
    if scope == "GLOBAL" and match == "exact_state_code":
        return "GLOBAL_EXACT"
    if scope == "GLOBAL" and match == "similar_state_code":
        return "GLOBAL_SIMILAR"
    if scope == "GLOBAL" and match == "vector_nearest":
        return "GLOBAL_VECTOR_NEAREST"
    return f"{scope}_{match.upper()}"


def _sample_count(rows: list[dict[str, Any]]) -> int:
    return int(sum(max(0.0, _to_float(row.get("sample_count"), 0)) for row in rows))


def _similarity_from_row(row: dict[str, Any], query_vector: dict[str, Any], state_code: Any) -> tuple[float, float, str]:
    match_type = str(row.get("_match_type") or "").lower()
    if match_type == "exact_state_code":
        return 100.0, 100.0, ""
    if match_type == "similar_state_code":
        return state_code_similarity(state_code, row.get("state_code")), 100.0, ""
    vector_result = state_vector_similarity(query_vector, row.get("state_vector_center"))
    similarity = _clamp(vector_result.get("similarity"), 0)
    confidence = _clamp(vector_result.get("similarity_confidence"), 0)
    warning = str(vector_result.get("warning") or "")
    if confidence <= 0:
        fallback = row.get("avg_similarity", 60)
        similarity = _clamp(_probability_percent(fallback) if _to_float(fallback, 0) <= 1 else fallback, 60)
        warning = "state_vector_center 缺失，向量近邻使用 avg_similarity fallback。"
    return similarity, max(25.0, confidence), warning


def _select_similar_state_rows(rows: list[dict[str, Any]], state_code: Any, top_k: int) -> list[dict[str, Any]]:
    candidates = []
    for row in rows:
        similarity = state_code_similarity(state_code, row.get("state_code"))
        if similarity >= SIMILARITY_MINIMUM and str(row.get("state_code")) != str(state_code):
            candidates.append({**row, "_state_code_similarity": similarity})
    candidates.sort(key=lambda row: (_to_float(row.get("_state_code_similarity"), 0) >= SIMILARITY_PRIORITY, _to_float(row.get("_state_code_similarity"), 0), _to_float(row.get("sample_count"), 0)), reverse=True)
    return candidates[: max(top_k * 3, 30)]


def _select_vector_nearest_rows(rows: list[dict[str, Any]], query_vector: dict[str, Any], top_k: int) -> tuple[list[dict[str, Any]], list[str]]:
    warnings: list[str] = []
    enriched: list[dict[str, Any]] = []
    for row in rows:
        result = state_vector_similarity(query_vector, row.get("state_vector_center"))
        similarity = _clamp(result.get("similarity"), 0)
        warning = str(result.get("warning") or "")
        if _clamp(result.get("similarity_confidence"), 0) <= 0:
            similarity, warning = _vector_similarity(query_vector, row.get("state_vector_center"), row.get("avg_similarity", 60))
        if warning and warning not in warnings:
            warnings.append(warning)
        if similarity >= SIMILARITY_MINIMUM:
            enriched.append({**row, "_pre_similarity": similarity})
    if not enriched:
        for row in rows:
            similarity, warning = _vector_similarity(query_vector, row.get("state_vector_center"), row.get("avg_similarity", 60))
            if warning and warning not in warnings:
                warnings.append(warning)
            if similarity >= VECTOR_LOW_SAMPLE_MINIMUM:
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
        similarity, similarity_confidence, warning = _similarity_from_row(row, query_vector, state_code)
        if warning and warning not in warnings:
            warnings.append(warning)
        sample_count = max(0.0, _to_float(row.get("sample_count"), 0))
        confidence = _clamp(row.get("confidence"), 0)
        data_quality = _clamp(row.get("data_quality"), 50)
        state_distance = 0.0 if str(row.get("state_code")) == str(state_code) else state_code_distance(state_code, row.get("state_code"))
        match_weight = MATCH_TYPE_WEIGHTS.get(_scope_match_key(str(row.get("_scope_type") or ""), str(row.get("_match_type") or "")), 0.35)
        quality_factor = max(0.05, confidence / 100 * data_quality / 100 * similarity_confidence / 100)
        basis = max(1.0, math.sqrt(max(sample_count, 1.0))) * match_weight * max(0.05, similarity / 100) * quality_factor
        rank_score = similarity * 2 + min(sample_count, 1000) / 35 + confidence * 0.5 + data_quality * 0.25 + match_weight * 30 - state_distance * 20
        enriched.append({
            **row,
            "_similarity": similarity,
            "_similarity_confidence": similarity_confidence,
            "_state_distance": state_distance,
            "_match_weight": match_weight,
            "_effective_basis": basis,
            "_rank_score": rank_score,
        })
    enriched.sort(key=lambda item: (_to_float(item.get("_rank_score"), 0), _to_float(item.get("sample_count"), 0)), reverse=True)
    selected = enriched[: max(1, top_k * 3)]
    total_basis = sum(max(0.0001, _to_float(row.get("_effective_basis"), 0)) for row in selected)
    def weighted(field: str) -> float:
        if total_basis <= 0:
            return 0.0
        return sum(_row_metric(row, field) * max(0.0001, _to_float(row.get("_effective_basis"), 0)) for row in selected) / total_basis

    sample_count = int(sum(max(0.0, _to_float(row.get("sample_count"), 0)) for row in selected))
    metrics = {field: round(weighted(field), 6) for field in METRIC_FIELDS}
    confidence = sum(_clamp(row.get("confidence"), 0) * max(0.0001, _to_float(row.get("_effective_basis"), 0)) for row in selected) / max(total_basis, 0.0001)
    data_quality = sum(_clamp(row.get("data_quality"), 50) * max(0.0001, _to_float(row.get("_effective_basis"), 0)) for row in selected) / max(total_basis, 0.0001)
    similarity = sum(_to_float(row.get("_similarity"), 0) * max(0.0001, _to_float(row.get("_effective_basis"), 0)) for row in selected) / max(total_basis, 0.0001)
    exact_sample_count = _sample_count([row for row in selected if str(row.get("_match_type") or "").lower() == "exact_state_code"])
    similar_sample_count = _sample_count([row for row in selected if str(row.get("_match_type") or "").lower() == "similar_state_code"])
    vector_sample_count = _sample_count([row for row in selected if str(row.get("_match_type") or "").lower() == "vector_nearest"])
    used_layers = []
    for row in selected:
        layer = _layer_name(str(row.get("_scope_type") or ""), str(row.get("_match_type") or ""))
        if layer not in used_layers:
            used_layers.append(layer)
    used_layers.sort(key=lambda item: LAYER_ORDER.index(item) if item in LAYER_ORDER else len(LAYER_ORDER))
    expansion_note = "精确状态样本充足，主要使用精确经验。"
    if exact_sample_count < 300 and (similar_sample_count or vector_sample_count):
        expansion_note = "精确状态样本较少，系统已扩展到相似状态与向量近邻，经验结果为扩展参考。"
    elif not exact_sample_count and sample_count:
        expansion_note = "未命中精确状态，系统使用相似状态或状态向量近邻作为扩展参考。"
    aggregate = {
        **metrics,
        "matched_sample_count": sample_count,
        "sample_count": sample_count,
        "exact_sample_count": exact_sample_count,
        "similar_state_sample_count": similar_sample_count,
        "vector_nearest_sample_count": vector_sample_count,
        "total_matched_sample_count": sample_count,
        "confidence": round(confidence, 2),
        "data_quality": round(data_quality, 2),
        "similarity": round(similarity, 2),
        "avg_similarity": round(similarity, 2),
        "used_match_layers": used_layers,
        "match_expansion_note": expansion_note,
        "exact_state_code": any(str(row.get("state_code")) == str(state_code) for row in selected),
        "matched_state_code": str(selected[0].get("state_code") or "") if selected else "",
        "top_matches": [
            {
                "symbol": row.get("symbol"),
                "symbol_group": row.get("symbol_group"),
                "state_code": row.get("state_code"),
                "sample_count": int(_to_float(row.get("sample_count"), 0)),
                "similarity": row.get("_similarity"),
                "match_type": row.get("_match_type"),
                "match_weight": row.get("_match_weight"),
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
    experience_version: str | None,
    exact_filters: list[tuple[str, str, Any]],
    broad_filters: list[tuple[str, str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[str], list[str]]:
    exact = load_experience_level_records(level, experience_library_path, version=experience_version, filters=exact_filters)
    warnings = list(exact.get("warnings") or [])
    errors = list(exact.get("errors") or [])
    exact_rows = list(exact.get("records") or [])
    broad = load_experience_level_records(level, experience_library_path, version=experience_version, filters=broad_filters)
    warnings.extend(str(item) for item in list(broad.get("warnings") or []) if item not in warnings)
    errors.extend(str(item) for item in list(broad.get("errors") or []) if item not in errors)
    return exact_rows, list(broad.get("records") or []), warnings, errors


def _build_expanded_rows(
    rows: list[dict[str, Any]],
    exact_rows: list[dict[str, Any]],
    query_vector: dict[str, Any],
    state_code: Any,
    scope_type: str,
    top_k: int,
) -> tuple[list[dict[str, Any]], list[str], dict[str, int]]:
    warnings: list[str] = []
    expanded: list[dict[str, Any]] = []
    seen: set[tuple[Any, Any, Any]] = set()

    for row in exact_rows:
        key = _row_identity(row)
        if key in seen:
            continue
        seen.add(key)
        expanded.append({**row, "_match_type": "exact_state_code", "_scope_type": scope_type})

    exact_sample_count = _sample_count(expanded)
    include_similar = exact_sample_count < 300
    include_vector = exact_sample_count < 100

    if include_similar:
        for row in _select_similar_state_rows(rows, state_code, top_k):
            key = _row_identity(row)
            if key in seen:
                continue
            seen.add(key)
            expanded.append({**row, "_match_type": "similar_state_code", "_scope_type": scope_type})

    if include_vector:
        vector_rows, vector_warnings = _select_vector_nearest_rows(rows, query_vector, top_k)
        warnings.extend(item for item in vector_warnings if item not in warnings)
        for row in vector_rows:
            key = _row_identity(row)
            if key in seen:
                continue
            seen.add(key)
            expanded.append({**row, "_match_type": "vector_nearest", "_scope_type": scope_type})

    counts = {
        "exact_sample_count": _sample_count([row for row in expanded if row.get("_match_type") == "exact_state_code"]),
        "similar_state_sample_count": _sample_count([row for row in expanded if row.get("_match_type") == "similar_state_code"]),
        "vector_nearest_sample_count": _sample_count([row for row in expanded if row.get("_match_type") == "vector_nearest"]),
    }
    return expanded, warnings, counts


def _match_level(level: str, query: dict[str, Any], experience_library_path: str | None, experience_version: str | None, top_k: int) -> dict[str, Any]:
    symbol = str(query.get("symbol") or "").upper()
    state_code = query.get("state_code")
    groups = _group_query_order(query)
    query_vector = query.get("state_vector") if isinstance(query.get("state_vector"), dict) else {}
    if level == "symbol_level":
        scope_type = "SYMBOL"
        exact_rows, rows, warnings, errors = _read_candidate_rows(
            level,
            experience_library_path,
            experience_version,
            [("symbol", "==", symbol), ("state_code", "==", state_code)],
            [("symbol", "==", symbol)],
        )
        rows = [row for row in rows if _scope_matches(row, {"symbol"}) and str(row.get("symbol") or "").upper() == symbol]
        exact_rows = [row for row in exact_rows if _scope_matches(row, {"symbol"}) and str(row.get("symbol") or "").upper() == symbol]
        used_group = ""
    elif level == "group_level":
        scope_type = "GROUP"
        rows = []
        exact_rows = []
        used_group = ""
        warnings = []
        errors = []
        for group in groups:
            found_exact, found, level_warnings, level_errors = _read_candidate_rows(
                level,
                experience_library_path,
                experience_version,
                [("symbol_group", "==", group), ("state_code", "==", state_code)],
                [("symbol_group", "==", group)],
            )
            warnings.extend(item for item in level_warnings if item not in warnings)
            errors.extend(item for item in level_errors if item not in errors)
            rows = [row for row in found if _scope_matches(row, {"group", "symbol_group"}) and str(row.get("symbol_group") or "") == group]
            exact_rows = [row for row in found_exact if _scope_matches(row, {"group", "symbol_group"}) and str(row.get("symbol_group") or "") == group]
            if rows:
                used_group = group
                break
    else:
        scope_type = "GLOBAL"
        used_group = ""
        exact_rows, rows, warnings, errors = _read_candidate_rows(
            level,
            experience_library_path,
            experience_version,
            [("state_code", "==", state_code)],
            [],
        )
        rows = [row for row in rows if _scope_matches(row, {"global"})]
        exact_rows = [row for row in exact_rows if _scope_matches(row, {"global"})]

    if errors and not rows:
        return {**_empty_level(scope_type, "该层经验读取失败。"), "available": False, "warnings": warnings, "errors": errors}
    if not rows:
        return {**_empty_level(scope_type), "available": not errors, "warnings": warnings, "errors": errors}
    expanded_rows, expansion_warnings, _pre_counts = _build_expanded_rows(rows, exact_rows, query_vector, state_code, scope_type, top_k)
    warnings.extend(item for item in expansion_warnings if item not in warnings)
    if not expanded_rows:
        return {**_empty_level(scope_type, "未找到相似状态或向量近邻经验。"), "available": True, "warnings": warnings, "errors": errors}
    aggregate, sim_warnings = _aggregate_rows(expanded_rows, query_vector, state_code, top_k)
    match_types = {str(row.get("_match_type") or "") for row in expanded_rows}
    if "exact_state_code" in match_types and len(match_types) == 1:
        match_type = "EXACT_STATE_CODE"
        reason = "命中 exact state_code，精确状态样本充足。"
    elif "exact_state_code" in match_types:
        match_type = "EXPANDED_SIMILAR"
        reason = "命中 exact state_code，且因样本不足扩展到相似状态或向量近邻。"
    elif "similar_state_code" in match_types:
        match_type = "SIMILAR_STATE_CODE"
        reason = "未命中 exact state_code，使用相似 state_code 与必要的向量近邻扩展。"
        warnings.append("exact state_code 未命中，已按相似 state_code 扩展。")
    else:
        match_type = "VECTOR_NEAREST"
        reason = "未命中 exact 与相似 state_code，使用 state_vector 最近邻扩展。"
        warnings.append("exact state_code 未命中，已按 state_vector 最近邻扩展。")
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
    used_match_layers = []
    for level in levels.values():
        for item in list(level.get("used_match_layers") or []):
            if item not in used_match_layers:
                used_match_layers.append(item)
    used_match_layers.sort(key=lambda item: LAYER_ORDER.index(item) if item in LAYER_ORDER else len(LAYER_ORDER))
    total_samples = int(sum(_to_float(level.get("matched_sample_count"), 0) for level in levels.values() if level.get("matched")))
    exact_samples = int(sum(_to_float(level.get("exact_sample_count"), 0) for level in levels.values() if level.get("matched")))
    similar_samples = int(sum(_to_float(level.get("similar_state_sample_count"), 0) for level in levels.values() if level.get("matched")))
    vector_samples = int(sum(_to_float(level.get("vector_nearest_sample_count"), 0) for level in levels.values() if level.get("matched")))
    global_samples = int(_to_float(levels.get("global_level", {}).get("matched_sample_count"), 0))
    effective_sample_count = 0.0
    effective_rule_notes: list[str] = []
    scope_factors = {"symbol_level": 1.0, "group_level": 0.55, "global_level": 0.25}
    for key, level in levels.items():
        if not level.get("matched"):
            continue
        similarity = _to_float(level.get("avg_similarity") or level.get("similarity"), 0)
        exact = _to_float(level.get("exact_sample_count"), 0)
        similar = _to_float(level.get("similar_state_sample_count"), 0)
        vector = _to_float(level.get("vector_nearest_sample_count"), 0)
        scope_factor = scope_factors.get(key, 0.25)
        if similarity < SIMILARITY_CUTOFF and exact <= 0:
            effective_sample_count += (similar + vector) * scope_factor * 0.10
            effective_rule_notes.append(f"{key} 相似度低于{SIMILARITY_CUTOFF:.0f}，只作为弱参考。")
            continue
        effective_sample_count += exact * scope_factor
        effective_sample_count += (similar + vector) * scope_factor * max(0.10, min(1.0, similarity / 100)) * 0.50
    avg_similarity = 0.0
    if total_samples > 0:
        avg_similarity = sum(_to_float(level.get("avg_similarity") or level.get("similarity"), 0) * _to_float(level.get("matched_sample_count"), 0) for level in levels.values() if level.get("matched")) / total_samples
    expansion_notes = []
    for level in levels.values():
        note = str(level.get("match_expansion_note") or "")
        if note and note not in expansion_notes:
            expansion_notes.append(note)
    expansion_note = "；".join(expansion_notes[:3])
    result.update(
        {
            "matched": bool(matched_layers),
            "matched_layers": matched_layers,
            "matched_sample_count": total_samples,
            "total_matched_sample_count": total_samples,
            "exact_sample_count": exact_samples,
            "similar_state_sample_count": similar_samples,
            "vector_nearest_sample_count": vector_samples,
            "global_sample_count": global_samples,
            "effective_sample_count": round(effective_sample_count, 2),
            "similarity_cutoff": SIMILARITY_CUTOFF,
            "effective_sample_note": "；".join(effective_rule_notes),
            "avg_similarity": round(avg_similarity, 2),
            "used_match_layers": used_match_layers,
            "match_expansion_note": expansion_note,
            "experience_confidence": round(sum(_clamp(levels[key].get("confidence"), 0) * weights.get(key, 0) for key in levels), 2),
            "experience_data_quality": round(sum(_clamp(levels[key].get("data_quality"), 0) * weights.get(key, 0) for key in levels), 2),
            "layer_weights": weights,
        }
    )
    return result


def _sample_confidence_policy(sample_count: int, matched_layers: list[Any], avg_similarity: float = 0.0) -> dict[str, Any]:
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
        cap = 85.0 if avg_similarity < 80 else 100.0
        participation = "谨慎参与" if avg_similarity < 80 else "参与投票"
        note = "匹配样本较充足，经验委员根据历史概率、MFE/MAE 和回撤分布参与投票。"
    if sample_count >= 1000 and avg_similarity >= 75:
        level = "扩展样本充足"
        cap = max(cap, 80.0)
        participation = "参与投票"
        note = "扩展匹配样本充足且平均相似度达标，经验委员可正常参与，但结果属于扩展相似经验。"
    elif sample_count >= 300 and avg_similarity >= 80:
        participation = "谨慎参与" if not has_symbol else participation
        note = "匹配样本和平均相似度达标，经验委员可谨慎参与投票。"
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
            "base_rate": {"up": 33.33, "sideways": 33.33, "down": 33.33},
            "edge": 0,
            "edge_direction": "WAIT",
            "effective_sample_count": 0,
            "similarity_cutoff": SIMILARITY_CUTOFF,
            "cost_adjusted_expectancy": 0,
            "has_trade_edge": False,
            "no_edge_wait": True,
        }
    up30 = _to_float(blended.get("historical_30m_up_probability"), 0)
    down30 = _to_float(blended.get("historical_30m_down_probability"), 0)
    sideways30 = _to_float(blended.get("historical_30m_sideways_probability"), 0)
    avg30 = _to_float(blended.get("avg_return_30m"), 0)
    mfe90 = abs(_to_float(blended.get("mfe_p90"), 0))
    mae90 = abs(_to_float(blended.get("mae_p90"), 0))
    loss_probability = _to_float(blended.get("historical_loss_probability"), 0)
    trap_risk = _to_float(blended.get("trap_risk_avg"), _to_float((query.get("state_vector") or {}).get("trap_risk_score"), 0))
    matched_layers = list(blended.get("matched_layers") or [])
    sample_count = int(_to_float(blended.get("matched_sample_count"), 0))
    effective_sample_count = _to_float(blended.get("effective_sample_count"), 0)
    avg_similarity = _to_float(blended.get("avg_similarity"), 0)
    exact_sample_count = int(_to_float(blended.get("exact_sample_count"), 0))
    similar_sample_count = int(_to_float(blended.get("similar_state_sample_count"), 0))
    vector_sample_count = int(_to_float(blended.get("vector_nearest_sample_count"), 0))
    sample_policy = _sample_confidence_policy(sample_count, matched_layers, avg_similarity)
    base_up = _to_float(blended.get("base_30m_up_probability"), 0)
    base_down = _to_float(blended.get("base_30m_down_probability"), 0)
    base_sideways = _to_float(blended.get("base_30m_sideways_probability"), 0)
    if base_up <= 0 and isinstance(blended.get("_base_rate"), dict):
        base = blended.get("_base_rate") or {}
        base_up = _to_float(base.get("up"), 0)
        base_down = _to_float(base.get("down"), 0)
        base_sideways = _to_float(base.get("sideways"), 0)
    if base_up <= 0 and base_down <= 0:
        base_up = base_down = base_sideways = 33.33
    long_edge = up30 - base_up
    short_edge = down30 - base_down
    edge_direction = "LONG" if long_edge >= short_edge and long_edge > 0 else "SHORT" if short_edge > 0 else "WAIT"
    edge = max(long_edge, short_edge, 0.0)
    round_trip_cost = round_trip_cost_rate()
    cost_adjusted_expectancy = avg30 - round_trip_cost
    has_trade_edge = bool(edge >= EDGE_THRESHOLD and cost_adjusted_expectancy > 0 and effective_sample_count >= 30 and avg_similarity >= SIMILARITY_CUTOFF)
    raw_experience_confidence = _clamp(blended.get("experience_confidence"), 0)
    exact_ratio = exact_sample_count / max(sample_count, 1)
    global_ratio = _to_float(blended.get("global_sample_count"), 0) / max(sample_count, 1)
    if sample_policy.get("single_symbol_experience_missing"):
        raw_experience_confidence *= 0.85
    if exact_ratio < 0.20 and (similar_sample_count or vector_sample_count):
        raw_experience_confidence *= 0.82
    if vector_sample_count > exact_sample_count + similar_sample_count:
        raw_experience_confidence *= 0.90
    if "GLOBAL" in {str(layer) for layer in matched_layers} and "SYMBOL" not in {str(layer) for layer in matched_layers}:
        raw_experience_confidence *= 0.80
    elif global_ratio > 0.50:
        raw_experience_confidence *= 0.90
    confidence = min(raw_experience_confidence, _to_float(sample_policy.get("experience_confidence_cap"), 100))
    data_quality = _clamp(blended.get("experience_data_quality"), 0)
    current_integrity = _clamp((query.get("state_vector") or {}).get("data_integrity_score"), data_quality)
    sample_score = min(20.0, math.log10(max(sample_count, 1)) * 8)
    prob_edge = edge if edge_direction == "LONG" else -edge if edge_direction == "SHORT" else 0
    return_score = max(-15.0, min(15.0, avg30 * 5000))
    rr_score = max(0.0, min(20.0, (mfe90 / max(mae90, 0.0001)) * 8))
    score = _clamp(50 + prob_edge * 0.55 + return_score + rr_score + sample_score * 0.5 + data_quality * 0.10 - trap_risk * 0.18 - loss_probability * 0.22)
    if not has_trade_edge:
        score = min(score, 48.0)
        confidence = min(confidence, 45.0)
    abstain_reason = ""
    if sample_count < 30:
        vote = "ABSTAIN"
        direction = "WAIT"
        abstain_reason = "样本数不足 30，经验委员弃权，仅展示历史参考。"
    elif confidence < 30:
        vote = "ABSTAIN"
        direction = "WAIT"
        abstain_reason = "历史经验置信度低于 30，经验委员弃权，仅展示历史参考。"
    elif not has_trade_edge:
        vote = "ABSTAIN"
        direction = "WAIT"
        abstain_reason = "样本较多，但相对市场基准优势不足，接近平均概率，不具备明显交易优势。"
    elif cost_adjusted_expectancy <= 0:
        vote = "ABSTAIN"
        direction = "WAIT"
        abstain_reason = "成本后交易期望值小于等于0，经验委员等待。"
    elif data_quality < 35 or mae90 >= 0.08 or loss_probability >= 70:
        vote = "VETO" if mae90 >= 0.12 or data_quality < 20 else "OPPOSE"
        direction = "WAIT"
    elif edge_direction == "SHORT" and down30 >= 55:
        vote = "OPPOSE"
        direction = "SHORT"
    elif edge_direction == "LONG" and up30 >= 55 and avg30 > 0 and mae90 <= max(0.06, mfe90 * 2.2):
        vote = "SUPPORT" if score >= 70 and confidence >= 50 else "CAUTIOUS_SUPPORT"
        direction = "LONG"
    elif edge_direction == "LONG" and up30 >= 52:
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
    if exact_ratio < 0.20 and (similar_sample_count or vector_sample_count) and vote == "SUPPORT":
        vote = "CAUTIOUS_SUPPORT"
        sample_policy["experience_participation_status"] = "谨慎参与"
    if avg_similarity < SIMILARITY_CUTOFF and vote == "SUPPORT":
        vote = "CAUTIOUS_SUPPORT"
    if not has_trade_edge and vote in {"SUPPORT", "CAUTIOUS_SUPPORT"}:
        vote = "ABSTAIN"
        direction = "WAIT"
    layers = "、".join(str(item) for item in blended.get("matched_layers") or []) or "无"
    used_layers = "、".join(str(item) for item in blended.get("used_match_layers") or []) or "无"
    sample_note = str(sample_policy.get("sample_confidence_note") or "")
    abstain_text = f"{abstain_reason}" if abstain_reason else ""
    experience_kind = "精确经验" if exact_sample_count >= max(similar_sample_count + vector_sample_count, 1) else "扩展相似经验"
    reason = (
        f"匹配层级：{layers}，使用层级：{used_layers}，经验类型：{experience_kind}。"
        f"精确样本{exact_sample_count}，相似状态扩展样本{similar_sample_count}，向量近邻样本{vector_sample_count}，"
        f"总参考样本{int(_to_float(blended.get('matched_sample_count'), 0))}，平均相似度{avg_similarity:.1f}。"
        f"有效样本{effective_sample_count:.1f}，相似度门槛{SIMILARITY_CUTOFF:.0f}。"
        f"{sample_note}"
        f"30m上涨/震荡/下跌概率为{up30:.1f}%/{sideways30:.1f}%/{down30:.1f}%，"
        f"市场基准概率为{base_up:.1f}%/{base_sideways:.1f}%/{base_down:.1f}%，edge={edge:.1f}pct，方向{edge_direction}。"
        f"成本后期望值{cost_adjusted_expectancy * 100:.2f}%。"
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
        "avg_similarity": round(avg_similarity, 2),
        "exact_sample_count": exact_sample_count,
        "similar_state_sample_count": similar_sample_count,
        "vector_nearest_sample_count": vector_sample_count,
        "total_matched_sample_count": sample_count,
        "effective_sample_count": round(effective_sample_count, 2),
        "similarity_cutoff": SIMILARITY_CUTOFF,
        "base_rate": {"up": round(base_up, 2), "sideways": round(base_sideways, 2), "down": round(base_down, 2)},
        "edge": round(edge, 2),
        "edge_long": round(long_edge, 2),
        "edge_short": round(short_edge, 2),
        "edge_direction": edge_direction,
        "edge_threshold": EDGE_THRESHOLD,
        "cost_adjusted_expectancy": round(cost_adjusted_expectancy, 6),
        "round_trip_cost_rate": round(round_trip_cost, 6),
        "has_trade_edge": has_trade_edge,
        "no_edge_wait": bool(not has_trade_edge),
        "abstain_reason": abstain_reason,
        "reason": reason,
        **sample_policy,
    }


def match_experience(
    query: dict[str, Any],
    experience_library_path: str | None = None,
    top_k: int = 50,
    experience_version: str | None = None,
) -> dict[str, Any]:
    resolved_path, selected_version = resolve_experience_library_path(experience_library_path, experience_version)
    resolved_path_text = str(resolved_path)
    status = check_experience_library_available(resolved_path_text, version=selected_version)
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
            "experience_library_version": selected_version,
            "experience_version": selected_version,
            "data_sources": status.get("data_sources"),
            "experience_library_path": status.get("path") or get_default_experience_library_path(),
            "experience_library_status": status,
            "query": query,
            "symbol_level": _empty_level("SYMBOL", reason),
            "group_level": _empty_level("GROUP", reason),
            "global_level": _empty_level("GLOBAL", reason),
            "top_k": top_k,
        }

    levels = {
        "symbol_level": _match_level("symbol_level", query, resolved_path_text, selected_version, top_k),
        "group_level": _match_level("group_level", query, resolved_path_text, selected_version, top_k),
        "global_level": _match_level("global_level", query, resolved_path_text, selected_version, top_k),
    }
    weights = _effective_weights(levels)
    for key, weight in weights.items():
        levels[key]["weight"] = weight
    blended = _blend(levels, weights)
    global_level = levels.get("global_level") or {}
    if global_level.get("matched"):
        blended["_base_rate"] = {
            "up": _to_float(global_level.get("historical_30m_up_probability"), 33.33),
            "sideways": _to_float(global_level.get("historical_30m_sideways_probability"), 33.33),
            "down": _to_float(global_level.get("historical_30m_down_probability"), 33.33),
        }
        blended["base_30m_up_probability"] = blended["_base_rate"]["up"]
        blended["base_30m_sideways_probability"] = blended["_base_rate"]["sideways"]
        blended["base_30m_down_probability"] = blended["_base_rate"]["down"]
    else:
        blended["_base_rate"] = {"up": 33.33, "sideways": 33.33, "down": 33.33}
        blended["base_30m_up_probability"] = 33.33
        blended["base_30m_sideways_probability"] = 33.33
        blended["base_30m_down_probability"] = 33.33
    vote = _vote_from_blended(blended, query)
    all_warnings = []
    for level in levels.values():
        all_warnings.extend(str(item) for item in list(level.get("warnings") or []) if item not in all_warnings)
    return {
        "available": True,
        **blended,
        **vote,
        "experience_library_version": selected_version,
        "experience_version": selected_version,
        "data_sources": status.get("data_sources"),
        "experience_library_path": status.get("path") or get_default_experience_library_path(),
        "experience_library_status": status,
        "query": query,
        "symbol_level": levels["symbol_level"],
        "group_level": levels["group_level"],
        "global_level": levels["global_level"],
        "warnings": all_warnings,
        "top_k": top_k,
    }
