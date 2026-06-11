"""机会评分与风险归一化引擎。

本模块只做本地评分，不执行交易。评分分为原始机会分、风险分、扣分和最终机会分，
用于机会榜排序、快速预判和委员会解释。
"""

from __future__ import annotations

from typing import Any


RISK_NORMAL_MAX = 50
RISK_HIGH_START = 65
RISK_AUTO_BLOCK = 80
RISK_HARD_VETO = 85


def _to_float(value: Any, default: float = 0.0) -> float:
    try:
        if value is None:
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


def _clamp(value: float, minimum: int = 0, maximum: int = 100) -> int:
    return max(minimum, min(maximum, int(round(value))))


def _risk_level(score: int) -> str:
    if score < 30:
        return "低风险"
    if score < 50:
        return "正常风险"
    if score < 65:
        return "偏高风险"
    if score < 80:
        return "高风险"
    if score < 85:
        return "极高风险"
    return "极端风险"


def opportunity_level(score: int) -> str:
    if score <= 20:
        return "无机会"
    if score <= 40:
        return "弱机会"
    if score <= 60:
        return "观察机会"
    if score <= 79:
        return "较好机会"
    return "强机会"


def _risk_penalty(score: int) -> int:
    if score < 40:
        return 0
    if score < 50:
        return 5
    if score < 60:
        return 10
    if score < 65:
        return 15
    if score < 70:
        return 20
    if score < 80:
        return 30
    if score < 85:
        return 40
    return 100


def _score_cap(risk_score: int, data_quality: str, overheat_risk: int, orderflow_risk: int) -> tuple[int, str]:
    cap = 100
    status = "可交易候选"
    if risk_score >= RISK_HARD_VETO:
        return 0, "禁止开仓"
    if risk_score >= RISK_AUTO_BLOCK:
        cap, status = min(cap, 59), "极高风险观察"
    elif risk_score >= 70:
        cap, status = min(cap, 69), "高风险观察"
    elif risk_score >= RISK_HIGH_START:
        cap, status = min(cap, 75), "降仓观察"
    elif risk_score >= 50:
        cap, status = min(cap, 85), "谨慎候选"
    if data_quality == "partial":
        cap, status = min(cap, 75), "数据部分缺失"
    if data_quality == "poor":
        return 0, "数据不足"
    if overheat_risk >= 13:
        cap, status = min(cap, 75), "过热观察"
    if orderflow_risk >= 9:
        cap, status = min(cap, 78), "盘口反向观察"
    return cap, status


def action_advice(score: int, direction: str, risk_score: int, status: str | None = None) -> str:
    if status in {"禁止开仓", "数据不足"} or risk_score >= RISK_HARD_VETO:
        return "禁止开仓"
    if risk_score >= RISK_AUTO_BLOCK:
        return "高风险观察"
    if score >= 82 and risk_score < RISK_HIGH_START:
        return "重点候选"
    if score >= 75:
        return "谨慎候选" if direction in {"多头", "空头"} else "等待确认"
    if score >= 60:
        return "观察等待"
    return "暂不参与"


def _derive_risk_from_breakdown(item: dict[str, Any]) -> tuple[int, dict[str, Any]]:
    existing = item.get("risk_breakdown")
    if isinstance(existing, dict) and existing:
        total = sum(int(_to_float(existing.get(key), 0)) for key in [
            "volatility_risk",
            "overheat_risk",
            "funding_risk",
            "crowding_risk",
            "liquidation_risk",
            "orderflow_risk",
            "data_quality_risk",
            "combo_risk_boost",
        ])
        return _clamp(total), dict(existing)
    score = int(_to_float(item.get("risk_score"), 50))
    return score, {
        "volatility_risk": min(15, max(0, score // 7)),
        "overheat_risk": min(15, max(0, score // 8)),
        "funding_risk": 2,
        "crowding_risk": 2,
        "liquidation_risk": min(20, max(0, score // 6)),
        "orderflow_risk": 3,
        "data_quality_risk": 0,
        "combo_risk_boost": 0,
        "main_risk_sources": ["兼容旧评分输入，等待风险拆解数据。"],
    }


def calculate_opportunity_scores(item: dict[str, Any]) -> dict[str, Any]:
    """计算原始机会分、风险扣分和最终机会分。"""
    trend_score = int(_to_float(item.get("trend_score"), 50))
    capital_score = int(_to_float(item.get("capital_score"), 50))
    structure_score = int(_to_float(item.get("structure_score"), 50))
    whale_score = int(_to_float(item.get("whale_score"), 0))
    liquidity_score = int(_to_float(item.get("liquidity_score"), _to_float(item.get("volume_score"), 50)))
    tradeability_score = int(_to_float(item.get("tradeability_score"), 75))
    change = _to_float(item.get("price_change_percent"))
    risk_score, risk_breakdown = _derive_risk_from_breakdown(item)
    data_quality = str(item.get("data_quality") or "good")

    trend_opportunity = _clamp(trend_score * 0.25, 0, 25)
    capital_opportunity = _clamp(capital_score * 0.20, 0, 20)
    structure_opportunity = _clamp(structure_score * 0.15, 0, 15)
    orderflow_opportunity = _clamp(whale_score * 0.15, 0, 15)
    liquidity_opportunity = _clamp(liquidity_score * 0.10, 0, 10)
    context_opportunity = _clamp(tradeability_score * 0.15, 0, 15)

    raw_score = _clamp(
        trend_opportunity
        + capital_opportunity
        + structure_opportunity
        + orderflow_opportunity
        + liquidity_opportunity
        + context_opportunity
    )

    long_bias = 7 if change > 0 else -7 if change < -3 else 0
    short_bias = 7 if change < 0 else -7 if change > 3 else 0
    base_short = _clamp((100 - trend_score) * 0.25 + capital_opportunity + structure_opportunity + orderflow_opportunity + liquidity_opportunity + context_opportunity)
    raw_long_score = _clamp(raw_score + long_bias)
    raw_short_score = _clamp(base_short + short_bias)
    if raw_long_score > raw_short_score + 8:
        direction = "多头"
        directional_raw = raw_long_score
    elif raw_short_score > raw_long_score + 8:
        direction = "空头"
        directional_raw = raw_short_score
    else:
        direction = "观察"
        directional_raw = max(raw_long_score, raw_short_score)

    risk_penalty = _risk_penalty(risk_score)
    overheat_risk = int(_to_float(risk_breakdown.get("overheat_risk"), 0))
    overheat_penalty = 10 if overheat_risk >= 13 else 6 if overheat_risk >= 10 else 3 if overheat_risk >= 7 else 0
    data_quality_penalty = 35 if data_quality == "poor" else 12 if data_quality == "partial" else 0
    score_cap, status = _score_cap(risk_score, data_quality, overheat_risk, int(_to_float(risk_breakdown.get("orderflow_risk"), 0)))
    adjusted_score = directional_raw - risk_penalty - overheat_penalty - data_quality_penalty
    final_score = _clamp(min(adjusted_score, score_cap))

    opportunity_breakdown = {
        "trend_opportunity": trend_opportunity,
        "capital_opportunity": capital_opportunity,
        "structure_opportunity": structure_opportunity,
        "orderflow_opportunity": orderflow_opportunity,
        "liquidity_opportunity": liquidity_opportunity,
        "tradeability_opportunity": context_opportunity,
        "main_opportunity_sources": [
            f"趋势机会 {trend_opportunity}/25",
            f"资金机会 {capital_opportunity}/20",
            f"结构机会 {structure_opportunity}/15",
            f"盘口大单机会 {orderflow_opportunity}/15",
        ],
    }

    return {
        "raw_opportunity_score": directional_raw,
        "final_opportunity_score": final_score,
        "opportunity_score": final_score,
        "long_score": _clamp(min(raw_long_score - risk_penalty, score_cap)),
        "short_score": _clamp(min(raw_short_score - risk_penalty, score_cap)),
        "risk_score": risk_score,
        "risk_level": _risk_level(risk_score),
        "risk_penalty": risk_penalty,
        "overheat_penalty": overheat_penalty,
        "data_quality_penalty": data_quality_penalty,
        "score_cap": score_cap,
        "opportunity_status": status,
        "opportunity_level": opportunity_level(final_score),
        "direction": direction,
        "advice": action_advice(final_score, direction, risk_score, status),
        "risk_breakdown": risk_breakdown,
        "opportunity_breakdown": opportunity_breakdown,
    }
