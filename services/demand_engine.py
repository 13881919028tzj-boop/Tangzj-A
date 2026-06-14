"""Demand quantification engine for AI_MODEL 9.2.

The engine is intentionally rule based.  It converts current market inputs into
buy demand, sell supply, urgency, sustainability, and trap-risk scores without
calling external AI services.
"""

from __future__ import annotations

from typing import Any


def clamp(value: Any, low: float = 0.0, high: float = 100.0) -> float:
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        numeric = low
    return max(low, min(high, numeric))


def _to_float(value: Any, default: float = 0.0) -> float:
    try:
        if value is None:
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


def _nested(data: dict[str, Any] | None, *keys: str, default: Any = None) -> Any:
    cur: Any = data or {}
    for key in keys:
        if not isinstance(cur, dict):
            return default
        cur = cur.get(key)
    return default if cur is None else cur


def _last_price(ticker: dict[str, Any] | None) -> float:
    ticker = ticker or {}
    return _to_float(ticker.get("last_price") or ticker.get("price") or ticker.get("lastPrice"), 0.0)


def _price_change_pct(ticker: dict[str, Any] | None, rows: list[dict[str, Any]] | None) -> float:
    ticker = ticker or {}
    for key in ("price_change_percent", "priceChangePercent", "change_24h"):
        if ticker.get(key) is not None:
            return _to_float(ticker.get(key), 0.0)
    rows = rows or []
    if len(rows) >= 2:
        last = _to_float(rows[-1].get("close"), 0.0)
        prev = _to_float(rows[-2].get("close"), last)
        if prev:
            return (last - prev) / prev * 100
    return 0.0


def _recent_volume_ratio(rows: list[dict[str, Any]] | None, lookback: int = 20) -> float:
    rows = rows or []
    if len(rows) < 3:
        return 1.0
    recent = rows[-min(5, len(rows)) :]
    base = rows[-min(lookback, len(rows)) :]
    recent_avg = sum(_to_float(r.get("volume"), 0.0) for r in recent) / max(len(recent), 1)
    base_avg = sum(_to_float(r.get("volume"), 0.0) for r in base) / max(len(base), 1)
    if base_avg <= 0:
        return 1.0
    return recent_avg / base_avg


def _oi_change(derivatives: dict[str, Any] | None) -> float:
    changes = _nested(derivatives, "oi", "changes", default={}) or {}
    for key in ("5m", "15m", "1h", "change_pct", "change_percent"):
        if changes.get(key) is not None:
            return _to_float(changes.get(key), 0.0)
    for key in ("change_pct", "change_percent"):
        value = _nested(derivatives, "oi", key)
        if value is not None:
            return _to_float(value, 0.0)
    return 0.0


def _funding_rate(derivatives: dict[str, Any] | None) -> float:
    return _to_float(_nested(derivatives, "funding", "rate", default=0.0), 0.0)


def _orderbook_bias(orderbook_analysis: dict[str, Any] | None) -> float:
    data = orderbook_analysis or {}
    for key in ("imbalance", "depth_imbalance", "bid_ask_imbalance"):
        if data.get(key) is not None:
            return clamp(_to_float(data.get(key), 0.0), -100, 100)
    bid = _to_float(data.get("bid_depth") or data.get("bid_total") or data.get("bid_volume"), 0.0)
    ask = _to_float(data.get("ask_depth") or data.get("ask_total") or data.get("ask_volume"), 0.0)
    total = bid + ask
    if total <= 0:
        return 0.0
    return clamp((bid - ask) / total * 100, -100, 100)


def _spread_score(orderbook_analysis: dict[str, Any] | None) -> float:
    spread = _to_float((orderbook_analysis or {}).get("spread_pct") or (orderbook_analysis or {}).get("spread_percent"), 0.0)
    if spread <= 0:
        return 75.0
    return clamp(100 - spread * 800)


def _whale_bias(whale: dict[str, Any] | list[Any] | None) -> float:
    if isinstance(whale, list):
        buy = sum(1 for row in whale if "buy" in str(row).lower() or "买" in str(row))
        sell = sum(1 for row in whale if "sell" in str(row).lower() or "卖" in str(row))
        total = buy + sell
        return 0.0 if total <= 0 else clamp((buy - sell) / total * 100, -100, 100)
    data = whale or {}
    for key in ("net_buy_score", "whale_bias", "direction_score"):
        if data.get(key) is not None:
            return clamp(_to_float(data.get(key), 0.0), -100, 100)
    buy = _to_float(data.get("buy_amount") or data.get("buy_volume"), 0.0)
    sell = _to_float(data.get("sell_amount") or data.get("sell_volume"), 0.0)
    total = buy + sell
    if total <= 0:
        return 0.0
    return clamp((buy - sell) / total * 100, -100, 100)


def _structure_bias(signal_analysis: dict[str, Any] | None, local_strategy: dict[str, Any] | None) -> float:
    text = " ".join(
        str(x or "")
        for x in [
            (signal_analysis or {}).get("market_structure"),
            (signal_analysis or {}).get("suggestion"),
            (local_strategy or {}).get("action"),
            (local_strategy or {}).get("direction"),
        ]
    )
    if any(word in text for word in ("突破", "做多", "上涨", "long", "支撑")):
        return 25.0
    if any(word in text for word in ("跌破", "做空", "下跌", "short", "压力")):
        return -25.0
    return 0.0


def _candle_shadow_risk(rows: list[dict[str, Any]] | None) -> float:
    rows = rows or []
    if not rows:
        return 0.0
    row = rows[-1]
    high = _to_float(row.get("high"), 0.0)
    low = _to_float(row.get("low"), 0.0)
    open_ = _to_float(row.get("open"), 0.0)
    close = _to_float(row.get("close"), 0.0)
    rng = high - low
    if rng <= 0:
        return 0.0
    upper = high - max(open_, close)
    lower = min(open_, close) - low
    return clamp(max(upper, lower) / rng * 100)


def analyze_demand(
    *,
    ticker: dict[str, Any] | None = None,
    rows: list[dict[str, Any]] | None = None,
    derivatives: dict[str, Any] | None = None,
    orderbook_analysis: dict[str, Any] | None = None,
    whale: dict[str, Any] | list[Any] | None = None,
    signal_analysis: dict[str, Any] | None = None,
    local_strategy: dict[str, Any] | None = None,
) -> dict[str, Any]:
    price_change = _price_change_pct(ticker, rows)
    volume_ratio = _recent_volume_ratio(rows)
    oi_change = _oi_change(derivatives)
    funding = _funding_rate(derivatives)
    orderbook_bias = _orderbook_bias(orderbook_analysis)
    whale_bias = _whale_bias(whale)
    structure_bias = _structure_bias(signal_analysis, local_strategy)

    up_momentum = clamp(50 + price_change * 8)
    down_momentum = clamp(50 - price_change * 8)
    volume_score = clamp(45 + (volume_ratio - 1) * 35)
    oi_buy = clamp(50 + oi_change * 5 if price_change >= 0 else 50 - oi_change * 3)
    oi_sell = clamp(50 - oi_change * 5 if price_change < 0 else 50 + max(-oi_change, 0) * 5)
    bid_strength = clamp(50 + orderbook_bias / 2)
    ask_strength = clamp(50 - orderbook_bias / 2)
    whale_buy = clamp(50 + whale_bias / 2)
    whale_sell = clamp(50 - whale_bias / 2)
    structure_buy = clamp(50 + structure_bias)
    structure_sell = clamp(50 - structure_bias)

    buy_demand_score = clamp(
        up_momentum * 0.20
        + volume_score * 0.18
        + oi_buy * 0.18
        + bid_strength * 0.16
        + whale_buy * 0.14
        + structure_buy * 0.14
    )
    sell_supply_score = clamp(
        down_momentum * 0.20
        + volume_score * 0.18
        + oi_sell * 0.18
        + ask_strength * 0.16
        + whale_sell * 0.14
        + structure_sell * 0.14
    )
    net_demand_score = clamp(buy_demand_score - sell_supply_score, -100, 100)
    demand_score = clamp(50 + net_demand_score / 2)

    urgency_score = clamp(
        abs(price_change) * 8 * 0.25
        + abs(volume_ratio - 1) * 45 * 0.25
        + abs(orderbook_bias) * 0.20
        + abs(oi_change) * 5 * 0.15
        + max(abs(structure_bias), 15) * 0.15
    )
    funding_health = clamp(100 - abs(funding) * 50000)
    sustainability_score = clamp(
        oi_buy * 0.25
        + volume_score * 0.20
        + max(up_momentum, down_momentum) * 0.20
        + _spread_score(orderbook_analysis) * 0.15
        + funding_health * 0.10
        + max(structure_buy, structure_sell) * 0.10
    )
    trap_risk_score = clamp(
        max(0, price_change) * 5 * 0.15
        + (100 - volume_score) * 0.15
        + max(0, -oi_change) * 5 * 0.15
        + max(0, abs(funding) * 50000 - 50) * 0.15
        + max(0, -orderbook_bias) * 0.15
        + _candle_shadow_risk(rows) * 0.15
        + (100 - _spread_score(orderbook_analysis)) * 0.10
    )

    if net_demand_score >= 50:
        demand_direction = "LONG"
        demand_change = "买方明显占优"
    elif net_demand_score >= 20:
        demand_direction = "LONG"
        demand_change = "买方偏强"
    elif net_demand_score <= -50:
        demand_direction = "SHORT"
        demand_change = "卖方明显占优"
    elif net_demand_score <= -20:
        demand_direction = "SHORT"
        demand_change = "卖方偏强"
    else:
        demand_direction = "NEUTRAL"
        demand_change = "供需平衡"

    reason = (
        f"{demand_change}；买方需求{buy_demand_score:.1f}，卖方供给{sell_supply_score:.1f}，"
        f"净需求{net_demand_score:.1f}。"
    )
    if price_change > 0 and oi_change < 0:
        reason += " 价格上涨但OI下降，可能存在空头回补成分，买方需求已保守处理。"
    if trap_risk_score >= 65:
        reason += " 诱导风险偏高，需等待盘口和成交量继续确认。"

    return {
        "buy_demand_score": round(buy_demand_score, 2),
        "sell_supply_score": round(sell_supply_score, 2),
        "net_demand_score": round(net_demand_score, 2),
        "demand_score": round(demand_score, 2),
        "demand_direction": demand_direction,
        "demand_change": demand_change,
        "urgency_score": round(urgency_score, 2),
        "sustainability_score": round(sustainability_score, 2),
        "trap_risk_score": round(trap_risk_score, 2),
        "demand_reason": reason,
        "inputs": {
            "price_change_pct": round(price_change, 4),
            "volume_ratio": round(volume_ratio, 4),
            "oi_change": round(oi_change, 4),
            "funding_rate": funding,
            "orderbook_bias": round(orderbook_bias, 2),
            "whale_bias": round(whale_bias, 2),
        },
    }
