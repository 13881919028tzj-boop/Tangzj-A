"""Reserved matching interface for future experience-library lookups."""

from __future__ import annotations

from typing import Any

from services.experience_library_loader import check_experience_library_available, get_default_experience_library_path
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


def build_experience_query_from_cognition(
    symbol: str,
    market_cognition: dict[str, Any],
    ticker: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build the query payload future experience matching will consume."""
    cognition = market_cognition or {}
    profile = build_symbol_profile(symbol, ticker=ticker)
    state_vector = _safe_vector(cognition)
    return {
        "symbol": str(symbol or cognition.get("symbol") or "").upper(),
        "symbol_group": profile.get("symbol_group", "UNKNOWN"),
        "symbol_profile": profile,
        "state_code": cognition.get("state_code"),
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


def _empty_level(scope_type: str) -> dict[str, Any]:
    return {
        "scope_type": scope_type,
        "available": False,
        "matched": False,
        "matched_sample_count": 0,
        "confidence": 0,
        "weight": 0,
        "reason": "当前阶段只预留接口，尚未执行大规模经验匹配。",
    }


def match_experience(query: dict[str, Any], experience_library_path: str | None = None, top_k: int = 50) -> dict[str, Any]:
    """Reserved interface for three-level experience matching.

    Future implementation:
    1. Query symbol_level_experience by symbol + state_code + state_vector similarity.
    2. If symbol samples are insufficient, query group_level_experience by symbol_group.
    3. Query global_level_experience as market-wide fallback.
    4. Blend levels by sample_count and confidence using the 9.2.3 contract rules.
    """
    status = check_experience_library_available(experience_library_path)
    if not status.get("available"):
        return {
            "available": False,
            "matched": False,
            "vote": "ABSTAIN",
            "score": 0,
            "confidence": 0,
            "data_integrity_score": 0,
            "reason": "经验库未接入，当前经验委员弃权。",
            "experience_library_path": status.get("path") or get_default_experience_library_path(),
            "experience_library_status": status,
            "query": query,
            "symbol_level": _empty_level("SYMBOL"),
            "group_level": _empty_level("GROUP"),
            "global_level": _empty_level("GLOBAL"),
            "top_k": top_k,
        }
    return {
        "available": True,
        "matched": False,
        "vote": "ABSTAIN",
        "score": 0,
        "confidence": 0,
        "data_integrity_score": 0,
        "reason": "经验库文件已发现，但9.2.3仅预留读取接口，正式匹配将在9.4/9.5启用。",
        "experience_library_path": status.get("path") or get_default_experience_library_path(),
        "experience_library_status": status,
        "query": query,
        "symbol_level": {**_empty_level("SYMBOL"), "available": bool(status.get("symbol_level_found"))},
        "group_level": {**_empty_level("GROUP"), "available": bool(status.get("group_level_found"))},
        "global_level": {**_empty_level("GLOBAL"), "available": bool(status.get("global_level_found"))},
        "top_k": top_k,
    }
