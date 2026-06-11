"""清算热力区与爆仓风险分析引擎。

本模块不调用账户或交易接口。清算区基于公开行情、K线结构、盘口、
OI、Funding 和多空比进行估算，用于风险提示与市场结构辅助判断。
"""

from __future__ import annotations

from statistics import mean
from typing import Any


def _to_float(value: Any, default: float = 0.0) -> float:
    """安全转换为浮点数。"""
    try:
        if value is None:
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


def _fmt_price(value: float | None) -> str:
    """价格格式化。"""
    if value is None:
        return "待确认"
    if abs(value) >= 1000:
        return f"{value:,.2f}"
    if abs(value) >= 1:
        return f"{value:.4f}".rstrip("0").rstrip(".")
    return f"{value:.8f}".rstrip("0").rstrip(".")


def _fmt_percent(value: float | None) -> str:
    """百分比格式化。"""
    if value is None:
        return "待确认"
    return f"{value:.2f}%"


def _atr(rows: list[dict[str, Any]], window: int = 14) -> float:
    """估算平均真实波幅。"""
    if len(rows) < window + 1:
        return 0.0
    ranges = []
    previous_close = _to_float(rows[-window - 1].get("close"))
    for row in rows[-window:]:
        high = _to_float(row.get("high"))
        low = _to_float(row.get("low"))
        ranges.append(max(high - low, abs(high - previous_close), abs(low - previous_close)))
        previous_close = _to_float(row.get("close"))
    return mean(ranges) if ranges else 0.0


def _support_resistance(rows: list[dict[str, Any]], current_price: float) -> tuple[float | None, float | None]:
    """从近期高低点推测支撑压力。"""
    if len(rows) < 20 or current_price <= 0:
        return None, None
    recent = rows[-120:]
    lows = sorted({_to_float(row.get("low")) for row in recent if _to_float(row.get("low")) < current_price})
    highs = sorted({_to_float(row.get("high")) for row in recent if _to_float(row.get("high")) > current_price})
    support = lows[-1] if lows else min(_to_float(row.get("low")) for row in recent)
    resistance = highs[0] if highs else max(_to_float(row.get("high")) for row in recent)
    return support, resistance


def _distance_percent(price: float | None, current_price: float) -> float | None:
    """计算与现价距离百分比。"""
    if price is None or current_price <= 0:
        return None
    return abs(price - current_price) / current_price * 100


def _risk_level(score: int) -> str:
    if score <= 20:
        return "安全"
    if score <= 40:
        return "较安全"
    if score <= 60:
        return "中等风险"
    if score <= 80:
        return "高风险"
    return "极高风险"


def _hunt_level(probability: int) -> str:
    if probability >= 75:
        return "高"
    if probability >= 45:
        return "中"
    return "低"


def _zone(price: float, width: float, direction: str) -> dict[str, Any]:
    """生成价格区间。"""
    if direction == "upper":
        low = price
        high = price + width
    else:
        low = price - width
        high = price
    return {
        "low": low,
        "high": high,
        "text": f"{_fmt_price(low)} - {_fmt_price(high)}",
    }


def analyze_liquidation_risk(
    ticker: dict[str, Any] | None,
    rows: list[dict[str, Any]],
    derivatives: dict[str, Any] | None,
    orderbook_analysis: dict[str, Any] | None,
    signal_analysis: dict[str, Any] | None,
) -> dict[str, Any]:
    """生成清算热力区、挤仓风险、猎杀止损与爆仓风险评分。"""
    current_price = _to_float((ticker or {}).get("last_price"))
    if current_price <= 0 or len(rows) < 30:
        return {
            "ready": False,
            "current_price": current_price,
            "upper_zone": "等待数据",
            "lower_zone": "等待数据",
            "nearest_zone": "等待数据",
            "upper_distance": "待确认",
            "lower_distance": "待确认",
            "upper_strength": "待确认",
            "lower_strength": "待确认",
            "risk_score": 0,
            "risk_level": "待评估",
            "squeeze_state": "等待数据",
            "hunt_probability": "待评估",
            "hunt_explanation": "K线或价格数据不足，暂无法判断清算热力区。",
            "explanation": "等待更多行情数据同步。",
        }

    atr_value = _atr(rows)
    if atr_value <= 0:
        atr_value = current_price * 0.008
    support, resistance = _support_resistance(rows, current_price)
    ob = orderbook_analysis or {}
    derivatives = derivatives or {}
    oi = derivatives.get("oi") or {}
    funding = derivatives.get("funding") or {}
    long_short = derivatives.get("long_short") or {}
    changes = oi.get("changes") or {}
    oi_change_1h = _to_float(changes.get("1h"))
    oi_change_24h = _to_float(changes.get("24h"))
    funding_percent = _to_float(funding.get("rate")) * 100
    account_ratio = _to_float(long_short.get("account_ratio"), 1.0)
    trend_score = int(_to_float((signal_analysis or {}).get("trend_score"), 50))
    structure = str((signal_analysis or {}).get("market_structure", "等待数据"))
    buy_ratio = _to_float(ob.get("buy_ratio"))
    sell_ratio = _to_float(ob.get("sell_ratio"))

    upper_anchor = resistance or current_price + atr_value * 1.5
    lower_anchor = support or current_price - atr_value * 1.5
    large_ask = ob.get("large_ask") or {}
    large_bid = ob.get("large_bid") or {}
    if large_ask.get("price"):
        upper_anchor = max(upper_anchor, _to_float(large_ask.get("price")))
    if large_bid.get("price"):
        lower_anchor = min(lower_anchor, _to_float(large_bid.get("price")))

    zone_width = max(atr_value * 0.65, current_price * 0.003)
    upper_zone = _zone(upper_anchor, zone_width, "upper")
    lower_zone = _zone(lower_anchor, zone_width, "lower")
    upper_distance = _distance_percent(upper_anchor, current_price)
    lower_distance = _distance_percent(lower_anchor, current_price)

    upper_strength = 45
    lower_strength = 45
    if account_ratio < 0.8 or funding_percent < -0.03:
        upper_strength += 18
    if account_ratio > 1.4 or funding_percent > 0.03:
        lower_strength += 18
    if oi_change_1h > 2:
        upper_strength += 8
        lower_strength += 8
    if sell_ratio >= 60:
        upper_strength += 8
    if buy_ratio >= 60:
        lower_strength += 8
    if upper_distance is not None and upper_distance < 1:
        upper_strength += 12
    if lower_distance is not None and lower_distance < 1:
        lower_strength += 12
    upper_strength = max(0, min(100, int(round(upper_strength))))
    lower_strength = max(0, min(100, int(round(lower_strength))))

    if upper_distance is None or lower_distance is None:
        nearest_zone = "等待数据"
    elif upper_distance <= lower_distance:
        nearest_zone = f"上方空头清算区，距离约{_fmt_percent(upper_distance)}"
    else:
        nearest_zone = f"下方多头清算区，距离约{_fmt_percent(lower_distance)}"

    score = 25
    if oi_change_1h >= 2:
        score += 15
    if oi_change_1h >= 8:
        score += 12
    if abs(funding_percent) >= 0.03:
        score += 12
    if abs(funding_percent) >= 0.08:
        score += 18
    if account_ratio >= 1.5 or (0 < account_ratio <= 0.7):
        score += 15
    if account_ratio >= 2 or (0 < account_ratio <= 0.5):
        score += 15
    if max(upper_strength, lower_strength) >= 75:
        score += 12
    if min([value for value in [upper_distance, lower_distance] if value is not None] or [99]) < 1:
        score += 15
    if structure in {"突破", "跌破", "假突破", "加速上涨", "加速下跌"}:
        score += 10
    if oi_change_24h < -8:
        score += 8
    score = max(0, min(100, int(round(score))))

    if upper_strength >= 70 and trend_score >= 60 and (upper_distance or 99) <= 2:
        squeeze_state = "空头挤压风险"
        explanation = "当前价格接近上方空头清算密集区，若继续上涨，可能触发空头连环平仓。"
    elif lower_strength >= 70 and trend_score <= 45 and (lower_distance or 99) <= 2:
        squeeze_state = "多头踩踏风险"
        explanation = "当前价格接近下方多头清算密集区，若继续下跌，可能触发多头止损和平仓。"
    elif upper_strength >= 70 and lower_strength >= 70:
        squeeze_state = "高风险双向震荡"
        explanation = "上下方都存在较强清算压力，短线可能出现来回扫损。"
    else:
        squeeze_state = "正常"
        explanation = "当前清算压力未形成极端单边风险，但仍需关注邻近区间。"

    hunt_probability_score = 25
    if upper_distance is not None and upper_distance < 1.2:
        hunt_probability_score += 20
    if lower_distance is not None and lower_distance < 1.2:
        hunt_probability_score += 20
    if structure == "假突破":
        hunt_probability_score += 20
    if large_ask or large_bid:
        hunt_probability_score += 12
    if max(buy_ratio, sell_ratio) >= 65:
        hunt_probability_score += 10
    hunt_probability_score = max(0, min(100, int(round(hunt_probability_score))))
    hunt_probability = _hunt_level(hunt_probability_score)

    if hunt_probability == "高":
        hunt_explanation = "近期高低点、盘口大单或清算区距离较近，存在扫止损后反向波动的可能。"
    elif hunt_probability == "中":
        hunt_explanation = "上方或下方存在一定止损集中区，突破或跌破时需要观察成交量确认。"
    else:
        hunt_explanation = "暂未发现明显猎杀止损区域，短线风险相对可控。"

    return {
        "ready": True,
        "current_price": current_price,
        "upper_zone": upper_zone["text"],
        "lower_zone": lower_zone["text"],
        "nearest_zone": nearest_zone,
        "upper_distance": _fmt_percent(upper_distance),
        "lower_distance": _fmt_percent(lower_distance),
        "upper_strength": f"{upper_strength} / 100",
        "lower_strength": f"{lower_strength} / 100",
        "risk_score": score,
        "risk_level": _risk_level(score),
        "squeeze_state": squeeze_state,
        "hunt_probability": hunt_probability,
        "hunt_explanation": hunt_explanation,
        "explanation": explanation,
    }
