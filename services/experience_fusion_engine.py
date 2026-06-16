"""Read-only fusion engine for multiple experience libraries."""

from __future__ import annotations

from typing import Any

from services.experience_library_loader import (
    EXPERIENCE_LIBRARY_LABELS,
    EXPERIENCE_LIBRARY_VERSIONS,
    get_experience_library_data_sources,
)
from services.experience_matcher import match_experience
from services.trading_cost_engine import round_trip_cost_rate


FUSION_LIBRARY_ORDER = ["current", "funding_v1", "oi_longshort_recent30_v1"]
BASE_FUSION_WEIGHTS = {
    frozenset({"current", "funding_v1", "oi_longshort_recent30_v1"}): {
        "funding_v1": 0.50,
        "current": 0.30,
        "oi_longshort_recent30_v1": 0.20,
    },
    frozenset({"current", "oi_longshort_recent30_v1"}): {
        "current": 0.70,
        "oi_longshort_recent30_v1": 0.30,
    },
    frozenset({"current", "funding_v1"}): {
        "funding_v1": 0.65,
        "current": 0.35,
    },
    frozenset({"funding_v1", "oi_longshort_recent30_v1"}): {
        "funding_v1": 0.70,
        "oi_longshort_recent30_v1": 0.30,
    },
}
FUSED_FIELDS = [
    "historical_30m_up_probability",
    "historical_30m_down_probability",
    "historical_30m_sideways_probability",
    "historical_60m_up_probability",
    "historical_60m_down_probability",
    "historical_60m_sideways_probability",
    "suggested_stop_loss",
    "suggested_take_profit_1",
    "suggested_take_profit_2",
    "avg_return_30m",
    "avg_return_60m",
    "mae_p90",
    "mfe_p90",
    "historical_loss_probability",
    "effective_sample_count",
    "edge",
    "edge_long",
    "edge_short",
    "cost_adjusted_expectancy",
    "round_trip_cost_rate",
]


def _to_float(value: Any, default: float = 0.0) -> float:
    try:
        if value is None:
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


def _clamp(value: Any, low: float = 0.0, high: float = 100.0) -> float:
    return max(low, min(high, _to_float(value, low)))


def _empty_result(reason: str, library_results: dict[str, dict[str, Any]] | None = None) -> dict[str, Any]:
    return {
        "available": False,
        "mode": "FUSED",
        "used_libraries": [],
        "library_weights": {},
        "library_results": library_results or {},
        "fused_vote": "ABSTAIN",
        "fused_direction": "WAIT",
        "fused_score": 0,
        "fused_confidence": 0,
        "fused_data_integrity_score": 0,
        "historical_30m_up_probability": 0,
        "historical_30m_down_probability": 0,
        "historical_30m_sideways_probability": 0,
        "historical_60m_up_probability": 0,
        "historical_60m_down_probability": 0,
        "historical_60m_sideways_probability": 0,
        "suggested_stop_loss": 0,
        "suggested_take_profit_1": 0,
        "suggested_take_profit_2": 0,
        "matched_sample_count": 0,
        "effective_sample_count": 0,
        "avg_similarity": 0,
        "base_rate": {"up": 33.33, "sideways": 33.33, "down": 33.33},
        "edge": 0,
        "similarity_cutoff": 75,
        "cost_adjusted_expectancy": 0,
        "has_trade_edge": False,
        "no_edge_wait": True,
        "reason": reason,
    }


def _base_weights(used_libraries: list[str]) -> dict[str, float]:
    used = frozenset(used_libraries)
    if used in BASE_FUSION_WEIGHTS:
        return dict(BASE_FUSION_WEIGHTS[used])
    if not used_libraries:
        return {}
    return {version: 1.0 / len(used_libraries) for version in used_libraries}


def _adjust_weights(base_weights: dict[str, float], matches: dict[str, dict[str, Any]]) -> tuple[dict[str, float], list[str]]:
    adjusted: dict[str, float] = {}
    notes: list[str] = []
    for version, weight in base_weights.items():
        item = matches.get(version) or {}
        confidence = _clamp(item.get("confidence"), 0, 100)
        sample_count = int(_to_float(item.get("matched_sample_count"), 0))
        factor = 1.0
        if confidence < 30:
            factor *= 0.50
            notes.append(f"{version} ExperienceConfidence 低于30，权重降低50%。")
        if sample_count < 30:
            factor *= 0.30
            notes.append(f"{version} 样本数低于30，只作为弱参考，权重降低70%。")
        adjusted[version] = weight * factor
    total = sum(adjusted.values())
    if total <= 0:
        return base_weights, notes
    return {version: value / total for version, value in adjusted.items()}, notes


def _weighted_average(matches: dict[str, dict[str, Any]], weights: dict[str, float], field: str) -> float:
    return sum(_to_float((matches.get(version) or {}).get(field), 0) * weight for version, weight in weights.items())


def _direction_bucket(direction: Any) -> str:
    text = str(direction or "").strip().upper()
    if text in {"LONG", "BUY", "多", "做多", "偏多"}:
        return "LONG"
    if text in {"SHORT", "SELL", "空", "做空", "偏空"}:
        return "SHORT"
    return "WAIT"


def _vote_from_fused(fused: dict[str, Any]) -> tuple[str, str, list[str]]:
    confidence = _to_float(fused.get("fused_confidence"), 0)
    up30 = _to_float(fused.get("historical_30m_up_probability"), 0)
    down30 = _to_float(fused.get("historical_30m_down_probability"), 0)
    avg_return = _to_float(fused.get("avg_return_30m"), 0)
    edge = _to_float(fused.get("edge"), 0)
    cost_adjusted_expectancy = _to_float(fused.get("cost_adjusted_expectancy"), avg_return - round_trip_cost_rate())
    has_trade_edge = bool(fused.get("has_trade_edge"))
    mae90 = abs(_to_float(fused.get("mae_p90"), 0))
    loss_probability = _to_float(fused.get("historical_loss_probability"), 0)
    notes: list[str] = []
    if confidence < 30:
        return "ABSTAIN", "WAIT", ["融合置信度低于30，经验委员弃权。"]
    if not has_trade_edge or edge < 4:
        return "ABSTAIN", "WAIT", ["样本较多，但相对市场基准优势不足，接近平均概率，不具备明显交易优势。"]
    if cost_adjusted_expectancy <= 0:
        return "ABSTAIN", "WAIT", ["融合经验成本后期望值小于等于0，经验委员等待。"]
    if mae90 >= 0.12 or loss_probability >= 70:
        return "VETO", "WAIT", ["融合经验显示风险过高，触发否决倾向。"]
    if down30 >= up30 + 8:
        direction = "SHORT" if down30 >= 60 else "WAIT"
        return "OPPOSE", direction, ["下跌概率明显高于上涨概率，经验委员反对做多。"]
    if up30 >= down30 + 8:
        if confidence >= 65 and avg_return > 0 and mae90 <= 0.06:
            return "SUPPORT", "LONG", ["上涨概率优势、平均收益为正且MAE可控。"]
        if confidence >= 50:
            return "CAUTIOUS_SUPPORT", "LONG", ["上涨概率高于下跌概率，融合经验谨慎支持。"]
    if 30 <= confidence < 50:
        return "CAUTIOUS_SUPPORT" if up30 > down30 else "OPPOSE", "LONG" if up30 > down30 else "WAIT", ["融合置信度处于30-50，仅允许谨慎支持或反对。"]
    notes.append("融合概率优势不够明确，经验委员保持谨慎。")
    return "ABSTAIN", "WAIT", notes


def build_fused_experience_result(query: dict[str, Any], *, top_k: int = 50) -> dict[str, Any]:
    """Match all configured libraries and build the fused committee result."""
    library_results: dict[str, dict[str, Any]] = {}
    for version in FUSION_LIBRARY_ORDER:
        try:
            library_results[version] = match_experience(query, experience_version=version, top_k=top_k)
        except Exception as exc:
            library_results[version] = {
                "available": False,
                "matched": False,
                "vote": "ABSTAIN",
                "direction": "WAIT",
                "confidence": 0,
                "matched_sample_count": 0,
                "reason": f"{version} 经验库匹配失败：{exc!r}",
                "experience_library_version": version,
                "data_sources": get_experience_library_data_sources(version),
            }

    used_libraries = [
        version
        for version in FUSION_LIBRARY_ORDER
        if (library_results.get(version) or {}).get("available") and (library_results.get(version) or {}).get("matched")
    ]
    if not used_libraries:
        return _empty_result("三个经验库均不可用或未命中，融合经验委员弃权。", library_results)

    base_weights = _base_weights(used_libraries)
    weights, weight_notes = _adjust_weights(base_weights, library_results)
    result = _empty_result("", library_results)
    result["available"] = True
    result["used_libraries"] = used_libraries
    result["library_weights"] = {version: round(weights.get(version, 0) * 100, 2) for version in used_libraries}
    for field in FUSED_FIELDS:
        result[field] = round(_weighted_average(library_results, weights, field), 6)
    result["matched_sample_count"] = int(sum(_to_float((library_results.get(version) or {}).get("matched_sample_count"), 0) for version in used_libraries))
    result["effective_sample_count"] = round(_weighted_average(library_results, weights, "effective_sample_count"), 2)
    result["avg_similarity"] = round(_weighted_average(library_results, weights, "avg_similarity"), 2)
    result["fused_score"] = round(_weighted_average(library_results, weights, "score"), 2)
    result["fused_confidence"] = round(_weighted_average(library_results, weights, "confidence"), 2)
    result["fused_data_integrity_score"] = round(_weighted_average(library_results, weights, "data_integrity_score"), 2)
    base_up = sum(_to_float(((library_results.get(version) or {}).get("base_rate") or {}).get("up"), 33.33) * weights.get(version, 0) for version in weights)
    base_sideways = sum(_to_float(((library_results.get(version) or {}).get("base_rate") or {}).get("sideways"), 33.33) * weights.get(version, 0) for version in weights)
    base_down = sum(_to_float(((library_results.get(version) or {}).get("base_rate") or {}).get("down"), 33.33) * weights.get(version, 0) for version in weights)
    result["base_rate"] = {"up": round(base_up, 2), "sideways": round(base_sideways, 2), "down": round(base_down, 2)}
    result["similarity_cutoff"] = max(_to_float((library_results.get(version) or {}).get("similarity_cutoff"), 75) for version in used_libraries)
    result["edge"] = round(max(_to_float(result.get("historical_30m_up_probability"), 0) - base_up, _to_float(result.get("historical_30m_down_probability"), 0) - base_down, 0), 2)
    result["edge_direction"] = "LONG" if _to_float(result.get("historical_30m_up_probability"), 0) - base_up >= _to_float(result.get("historical_30m_down_probability"), 0) - base_down and result["edge"] > 0 else "SHORT" if result["edge"] > 0 else "WAIT"
    if not result.get("cost_adjusted_expectancy"):
        result["cost_adjusted_expectancy"] = round(_to_float(result.get("avg_return_30m"), 0) - round_trip_cost_rate(), 6)
    result["has_trade_edge"] = bool(result["edge"] >= 4 and _to_float(result.get("cost_adjusted_expectancy"), 0) > 0 and _to_float(result.get("effective_sample_count"), 0) >= 30 and _to_float(result.get("avg_similarity"), 0) >= _to_float(result.get("similarity_cutoff"), 75))
    result["no_edge_wait"] = not result["has_trade_edge"]

    directions = [_direction_bucket((library_results.get(version) or {}).get("direction")) for version in used_libraries]
    non_wait_directions = [item for item in directions if item != "WAIT"]
    direction_notes: list[str] = []
    if non_wait_directions and len(set(non_wait_directions)) == 1 and len(non_wait_directions) == len(used_libraries):
        result["fused_confidence"] = round(min(100, _to_float(result["fused_confidence"]) + 8), 2)
        direction_notes.append("三个可用经验库方向一致，融合置信度已上调。")
    elif len(set(directions)) > 1:
        result["fused_confidence"] = round(max(0, _to_float(result["fused_confidence"]) - 10), 2)
        direction_notes.append(f"经验库方向存在分歧：{', '.join(f'{v}={d}' for v, d in zip(used_libraries, directions))}。")

    vote, direction, vote_notes = _vote_from_fused(result)
    result["fused_vote"] = vote
    result["fused_direction"] = direction
    library_summaries = []
    for version in used_libraries:
        item = library_results.get(version) or {}
        library_summaries.append(
            f"{version}({EXPERIENCE_LIBRARY_LABELS.get(version, version)}) 权重{result['library_weights'].get(version, 0):.1f}%，"
            f"vote={item.get('vote', 'ABSTAIN')}/{item.get('direction', 'WAIT')}，"
            f"30m上涨{_to_float(item.get('historical_30m_up_probability'), 0):.1f}%，"
            f"60m上涨{_to_float(item.get('historical_60m_up_probability'), 0):.1f}%，"
            f"Confidence {_to_float(item.get('confidence'), 0):.1f}，样本{int(_to_float(item.get('matched_sample_count'), 0))}"
        )
    result["reason"] = (
        "融合经验委员读取 current、funding_v1、oi_longshort_recent30_v1。"
        + "；".join(library_summaries)
        + "。"
        + " ".join(weight_notes + direction_notes + vote_notes)
        + " oi_longshort_recent30_v1 是最近30天 OI / 多空比修正库，不是长期历史库。"
    )
    return result
