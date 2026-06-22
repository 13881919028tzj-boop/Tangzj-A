"""Support/resistance based exit planning for simulated trades."""

from __future__ import annotations

from statistics import median
from typing import Any


def _to_float(value: Any, default: float = 0.0) -> float:
    try:
        if value is None or value == "":
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


def _atr(rows: list[dict[str, Any]], period: int = 14) -> float:
    sample = rows[-period - 1 :]
    ranges: list[float] = []
    previous_close = 0.0
    for row in sample:
        high = _to_float(row.get("high"))
        low = _to_float(row.get("low"))
        close = _to_float(row.get("close"))
        if high <= 0 or low <= 0:
            continue
        if previous_close > 0:
            ranges.append(max(high - low, abs(high - previous_close), abs(low - previous_close)))
        else:
            ranges.append(high - low)
        previous_close = close
    return sum(ranges) / len(ranges) if ranges else 0.0


def _pivot_levels(rows: list[dict[str, Any]], window: int = 2) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    supports: list[dict[str, Any]] = []
    resistances: list[dict[str, Any]] = []
    if len(rows) < window * 2 + 3:
        return supports, resistances
    for index in range(window, len(rows) - window):
        row = rows[index]
        low = _to_float(row.get("low"))
        high = _to_float(row.get("high"))
        local = rows[index - window : index + window + 1]
        if low > 0 and low <= min(_to_float(item.get("low")) for item in local):
            supports.append({"price": low, "index": index, "volume": _to_float(row.get("volume"))})
        if high > 0 and high >= max(_to_float(item.get("high")) for item in local):
            resistances.append({"price": high, "index": index, "volume": _to_float(row.get("volume"))})
    return supports, resistances


def _cluster_levels(levels: list[dict[str, Any]], tolerance: float, total_rows: int) -> list[dict[str, Any]]:
    clusters: list[dict[str, Any]] = []
    for level in sorted(levels, key=lambda item: item["price"]):
        price = _to_float(level.get("price"))
        if price <= 0:
            continue
        matched = None
        for cluster in clusters:
            if abs(price - cluster["price"]) <= tolerance:
                matched = cluster
                break
        if matched is None:
            clusters.append(
                {
                    "price": price,
                    "touches": 1,
                    "last_index": int(level.get("index", 0)),
                    "volume_sum": _to_float(level.get("volume")),
                    "prices": [price],
                }
            )
            continue
        matched["prices"].append(price)
        matched["price"] = median(matched["prices"])
        matched["touches"] = int(matched["touches"]) + 1
        matched["last_index"] = max(int(matched["last_index"]), int(level.get("index", 0)))
        matched["volume_sum"] = _to_float(matched.get("volume_sum")) + _to_float(level.get("volume"))
    for cluster in clusters:
        recency = int(cluster["last_index"]) / max(total_rows - 1, 1)
        cluster["strength"] = round(int(cluster["touches"]) * 20 + recency * 35 + min(_to_float(cluster["volume_sum"]) / 1000, 25), 2)
    return sorted(clusters, key=lambda item: item["strength"], reverse=True)


def _nearest_levels(levels: list[dict[str, Any]], current_price: float, side: str) -> list[dict[str, Any]]:
    if side == "below":
        candidates = [level for level in levels if _to_float(level.get("price")) < current_price]
        return sorted(candidates, key=lambda item: current_price - _to_float(item.get("price")))
    candidates = [level for level in levels if _to_float(level.get("price")) > current_price]
    return sorted(candidates, key=lambda item: _to_float(item.get("price")) - current_price)


def _rr(entry: float, stop: float, target: float, direction: str) -> float:
    if entry <= 0 or stop <= 0 or target <= 0:
        return 0.0
    if direction == "short":
        risk = stop - entry
        reward = entry - target
    else:
        risk = entry - stop
        reward = target - entry
    return reward / risk if risk > 0 else 0.0


def build_structure_exit_plan(
    symbol: str,
    direction: str,
    current_price: float,
    rows: list[dict[str, Any]],
    risk_score: float = 50.0,
) -> dict[str, Any]:
    """Return structure-aware stop/take-profit plan.

    The plan is valid only when nearby support/resistance gives a sane risk
    distance and at least a usable first target. Callers should fall back to
    their dynamic R plan when ``valid`` is false.
    """
    price = _to_float(current_price)
    clean_rows = [row for row in rows if _to_float(row.get("high")) > 0 and _to_float(row.get("low")) > 0 and _to_float(row.get("close")) > 0]
    if price <= 0 or direction not in {"long", "short"} or len(clean_rows) < 30:
        return {"valid": False, "reason": "K线样本不足，使用动态R兜底。", "symbol": symbol}

    sample = clean_rows[-160:]
    avg_range = median([max(_to_float(row.get("high")) - _to_float(row.get("low")), 0.0) for row in sample[-40:]] or [0.0])
    atr = _atr(sample)
    tolerance = max(price * 0.0025, avg_range * 0.8, atr * 0.25)
    buffer = max(price * 0.001, atr * 0.18, avg_range * 0.35)
    raw_supports, raw_resistances = _pivot_levels(sample)
    supports = _cluster_levels(raw_supports, tolerance, len(sample))
    resistances = _cluster_levels(raw_resistances, tolerance, len(sample))
    below_supports = _nearest_levels(supports, price, "below")
    above_resistances = _nearest_levels(resistances, price, "above")
    min_stop_pct = 0.0035
    max_stop_pct = 0.04 if risk_score < 70 else 0.025

    if direction == "long":
        stop_level = below_supports[0] if below_supports else None
        tp1_level = above_resistances[0] if above_resistances else None
        tp2_level = above_resistances[1] if len(above_resistances) > 1 else None
        if not stop_level or not tp1_level:
            return {"valid": False, "reason": "缺少有效支撑或压力。", "symbol": symbol, "supports": below_supports[:3], "resistances": above_resistances[:3]}
        stop = _to_float(stop_level["price"]) - buffer
        tp1 = max(price + buffer, _to_float(tp1_level["price"]) - buffer * 0.25)
        fallback_tp2 = price + (price - stop) * 2.4
        tp2 = max(tp1 + buffer, (_to_float(tp2_level["price"]) - buffer * 0.25) if tp2_level else fallback_tp2)
    else:
        stop_level = above_resistances[0] if above_resistances else None
        tp1_level = below_supports[0] if below_supports else None
        tp2_level = below_supports[1] if len(below_supports) > 1 else None
        if not stop_level or not tp1_level:
            return {"valid": False, "reason": "缺少有效压力或支撑。", "symbol": symbol, "supports": below_supports[:3], "resistances": above_resistances[:3]}
        stop = _to_float(stop_level["price"]) + buffer
        tp1 = min(price - buffer, _to_float(tp1_level["price"]) + buffer * 0.25)
        fallback_tp2 = price - (stop - price) * 2.4
        tp2 = min(tp1 - buffer, (_to_float(tp2_level["price"]) + buffer * 0.25) if tp2_level else fallback_tp2)

    stop_pct = abs(price - stop) / price if price else 0.0
    rr1 = _rr(price, stop, tp1, direction)
    rr2 = _rr(price, stop, tp2, direction)
    if stop_pct < min_stop_pct:
        return {"valid": False, "reason": "结构止损太近，容易被噪音扫损。", "stop_pct": stop_pct, "symbol": symbol}
    if stop_pct > max_stop_pct:
        return {"valid": False, "reason": "结构止损太远，单笔风险过大。", "stop_pct": stop_pct, "symbol": symbol}
    if rr1 < 0.7 or rr2 <= rr1:
        return {"valid": False, "reason": "结构目标空间不足，盈亏比不合格。", "rr1": rr1, "rr2": rr2, "symbol": symbol}

    return {
        "valid": True,
        "symbol": symbol,
        "direction": direction,
        "source": "structure_levels",
        "stop_loss": stop,
        "take_profit_1": tp1,
        "take_profit_2": tp2,
        "stop_pct": stop_pct,
        "rr1": rr1,
        "rr2": rr2,
        "support": stop_level if direction == "long" else tp1_level,
        "resistance": tp1_level if direction == "long" else stop_level,
        "supports": below_supports[:3],
        "resistances": above_resistances[:3],
        "buffer": buffer,
        "atr": atr,
        "reason": "结构位止盈止损有效。",
    }
