"""全市场机会扫描引擎。

当前版本采用轻量扫描：复用 Binance 24hr ticker 全市场缓存，不额外高频请求
K线、OI 或大单接口，避免手机端卡顿和接口压力。后续可在这里分批接入
OI、Funding、多空比、大单异动等增强字段。
"""

from __future__ import annotations

import math
from typing import Any

from services.opportunity_score_engine import calculate_opportunity_scores


def _to_float(value: Any, default: float = 0.0) -> float:
    try:
        if value is None:
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


def _clamp(value: float, minimum: int = 0, maximum: int = 100) -> int:
    return max(minimum, min(maximum, int(round(value))))


def _volume_score(volume: float, max_volume: float) -> int:
    if volume <= 0 or max_volume <= 0:
        return 35
    raw = 35 + (math.log10(volume + 1) / max(math.log10(max_volume + 1), 1)) * 55
    return _clamp(raw)


def _market_state(change: float, volume_score: int, risk_score: int) -> str:
    if risk_score >= 85 and change > 0:
        return "高风险上涨"
    if risk_score >= 85 and change < 0:
        return "高风险下跌"
    if change >= 8 and volume_score >= 65:
        return "放量突破"
    if change >= 3:
        return "健康上涨"
    if change <= -8 and volume_score >= 65:
        return "放量下跌"
    if change <= -3:
        return "健康下跌"
    if abs(change) <= 1.2:
        return "震荡观望"
    return "异动观察"


def _risk_band(value: float, thresholds: tuple[float, float, float], max_score: int) -> int:
    low, mid, high = thresholds
    if value <= low:
        return _clamp(value / max(low, 0.01) * max_score * 0.25, 0, max_score)
    if value <= mid:
        return _clamp(max_score * 0.25 + (value - low) / max(mid - low, 0.01) * max_score * 0.25, 0, max_score)
    if value <= high:
        return _clamp(max_score * 0.50 + (value - mid) / max(high - mid, 0.01) * max_score * 0.30, 0, max_score)
    return max_score


def _build_risk_breakdown(change: float, volume_score: int, trend_score: int, structure_score: int) -> tuple[int, dict[str, Any], str]:
    abs_change = abs(change)
    volatility_risk = _risk_band(abs_change, (2.5, 6, 12), 15)
    overheat_risk = _risk_band(max(0, abs_change - 2), (3, 8, 16), 15)
    funding_risk = 2
    crowding_risk = 2
    liquidation_risk = _risk_band(abs_change, (5, 11, 20), 20)
    orderflow_risk = _clamp(5 - max(0, volume_score - 55) * 0.06)
    data_quality_risk = 0
    combo_risk_boost = 0
    sources: list[str] = []

    healthy_trend = (
        3 <= abs_change <= 12
        and volume_score >= 62
        and structure_score >= 62
        and 35 <= trend_score <= 90
    )
    if healthy_trend:
        sources.append("放量趋势较健康，轻量风险模型限制过敏放大。")
    if abs_change >= 18 and volume_score < 60:
        combo_risk_boost += 12
        sources.append("短时大幅波动但成交额支持不足，疑似异常拉扯。")
    if abs_change >= 25:
        combo_risk_boost += 10
        sources.append("24小时涨跌幅极端，需防追高追空。")
    if volume_score <= 45 and abs_change >= 8:
        combo_risk_boost += 8
        sources.append("流动性偏弱且波动较大。")

    score = (
        volatility_risk
        + overheat_risk
        + funding_risk
        + crowding_risk
        + liquidation_risk
        + orderflow_risk
        + data_quality_risk
        + combo_risk_boost
    )
    high_risk_conditions = sum(
        [
            volatility_risk >= 12,
            overheat_risk >= 12,
            liquidation_risk >= 16,
            orderflow_risk >= 8,
            combo_risk_boost >= 15,
            abs_change >= 25,
        ]
    )
    if healthy_trend and high_risk_conditions <= 1:
        score = min(score, 50)
    if high_risk_conditions < 4:
        score = min(score, 84)
    diagnostic = "normal"
    breakdown = {
        "volatility_risk": volatility_risk,
        "overheat_risk": overheat_risk,
        "funding_risk": funding_risk,
        "crowding_risk": crowding_risk,
        "liquidation_risk": liquidation_risk,
        "orderflow_risk": orderflow_risk,
        "data_quality_risk": data_quality_risk,
        "combo_risk_boost": combo_risk_boost,
        "high_risk_conditions": high_risk_conditions,
        "main_risk_sources": sources or ["当前主要风险来自普通波动与价格位置，未发现极端组合风险。"],
        "funding_context": "轻量扫描未接入 Funding，按中性处理。",
        "crowding_context": "轻量扫描未接入多空比，按中性处理。",
        "liquidation_context": "轻量扫描仅按波动估算清算风险，完整清算热力区由委员会复核。",
        "healthy_trend_protection": healthy_trend,
    }
    return _clamp(score), breakdown, diagnostic


def _enrich_ticker(item: dict[str, Any], max_volume: float) -> dict[str, Any]:
    change = _to_float(item.get("price_change_percent"))
    volume = _to_float(item.get("quote_volume"))
    volume_score = _volume_score(volume, max_volume)
    trend_score = _clamp(50 + change * 4)
    structure_score = _clamp(50 + min(abs(change), 12) * 3 + (8 if volume_score >= 70 else 0))
    capital_score = _clamp(volume_score + (6 if abs(change) >= 4 else 0))
    whale_score = _clamp(volume_score * 0.55 + min(abs(change), 15) * 3)
    risk_score, risk_breakdown, diagnostic = _build_risk_breakdown(change, volume_score, trend_score, structure_score)
    liquidation_score = _clamp(risk_breakdown["liquidation_risk"] * 4)
    tradeability_score = _clamp(76 + (8 if volume_score >= 70 else 0) - (10 if volume_score < 45 else 0))

    row = {
        **item,
        "current_price": _to_float(item.get("last_price")),
        "volume_score": volume_score,
        "trend_score": trend_score,
        "capital_score": capital_score,
        "structure_score": structure_score,
        "risk_score": risk_score,
        "risk_breakdown": risk_breakdown,
        "risk_model_diagnostic": diagnostic,
        "whale_score": whale_score,
        "liquidation_score": liquidation_score,
        "liquidity_score": volume_score,
        "tradeability_score": tradeability_score,
        "data_quality": "good",
        "current_market_state": _market_state(change, volume_score, risk_score),
    }
    row.update(calculate_opportunity_scores(row))
    return row


def _dedupe_candidates(*groups: list[dict[str, Any]]) -> list[dict[str, Any]]:
    by_symbol: dict[str, dict[str, Any]] = {}
    for group in groups:
        for item in group:
            symbol = str(item.get("symbol", "")).upper()
            if symbol and symbol not in by_symbol:
                by_symbol[symbol] = item
    return list(by_symbol.values())


def scan_market_opportunities(tickers: list[dict[str, Any]], limit: int = 10) -> dict[str, list[dict[str, Any]]]:
    """生成强弱、机会、异动和高风险榜单。"""
    if not tickers:
        return {
            "strong": [],
            "weak": [],
            "long_opportunities": [],
            "short_opportunities": [],
            "abnormal": [],
            "high_risk": [],
        }

    sorted_volume = sorted(tickers, key=lambda item: _to_float(item.get("quote_volume")), reverse=True)
    sorted_gainers = sorted(tickers, key=lambda item: _to_float(item.get("price_change_percent")), reverse=True)
    sorted_losers = sorted(tickers, key=lambda item: _to_float(item.get("price_change_percent")))
    candidates = _dedupe_candidates(sorted_volume[:100], sorted_gainers[:50], sorted_losers[:50])
    max_volume = max((_to_float(item.get("quote_volume")) for item in candidates), default=0.0)
    rows = [_enrich_ticker(item, max_volume) for item in candidates]
    top10_risks = sorted((int(item.get("risk_score", 0) or 0) for item in rows), reverse=True)[:10]
    avg_top10_risk = sum(top10_risks) / len(top10_risks) if top10_risks else 0
    diagnostic = "normal"
    if avg_top10_risk > 80:
        diagnostic = "too_sensitive"
    elif avg_top10_risk < 20:
        diagnostic = "too_loose"
    for row in rows:
        row["risk_model_diagnostic"] = diagnostic
        if diagnostic == "too_sensitive":
            row.setdefault("risk_breakdown", {}).setdefault("diagnostic_note", "风险模型可能过度敏感，请检查阈值设置。")
        elif diagnostic == "too_loose":
            row.setdefault("risk_breakdown", {}).setdefault("diagnostic_note", "风险模型可能过于宽松，请检查阈值设置。")

    return {
        "strong": sorted(rows, key=lambda item: (item["trend_score"], item["quote_volume"]), reverse=True)[:limit],
        "weak": sorted(rows, key=lambda item: (100 - item["trend_score"], item["quote_volume"]), reverse=True)[:limit],
        "long_opportunities": sorted(rows, key=lambda item: (item["long_score"], item["final_opportunity_score"]), reverse=True)[:limit],
        "short_opportunities": sorted(rows, key=lambda item: (item["short_score"], item["final_opportunity_score"]), reverse=True)[:limit],
        "abnormal": sorted(
            rows,
            key=lambda item: (abs(_to_float(item.get("price_change_percent"))) * 5 + item["whale_score"]),
            reverse=True,
        )[:limit],
        "high_risk": sorted(rows, key=lambda item: item["risk_score"], reverse=True)[:limit],
    }
