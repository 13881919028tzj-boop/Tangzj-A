"""盘口订单簿分析器。

输出买盘强度、卖盘强度、买卖比、大单监控和多空倾向，供后续市场结构、机会榜和AI委员会复用。
"""

from __future__ import annotations

from typing import Any


def _sum_quantity(levels: list[dict[str, Any]]) -> float:
    """累加挂单数量。"""
    return sum(float(level.get("quantity", 0) or 0) for level in levels)


def _with_cumulative(levels: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """为盘口档位添加累计数量。"""
    cumulative = 0.0
    result = []
    for level in levels:
        cumulative += float(level.get("quantity", 0) or 0)
        item = dict(level)
        item["cumulative"] = cumulative
        item["cumulative_text"] = compact_quantity(cumulative)
        result.append(item)
    return result


def compact_quantity(value: float) -> str:
    """紧凑显示数量，不固定两位小数。"""
    if abs(value) >= 1_000_000_000:
        return f"{value / 1_000_000_000:.3f}B".rstrip("0").rstrip(".")
    if abs(value) >= 1_000_000:
        return f"{value / 1_000_000:.3f}M".rstrip("0").rstrip(".")
    if abs(value) >= 1_000:
        return f"{value / 1_000:.3f}K".rstrip("0").rstrip(".")
    text = f"{value:.8f}".rstrip("0").rstrip(".")
    return text or "0"


def _detect_large_order(levels: list[dict[str, Any]], current_price: float | None) -> dict[str, Any] | None:
    """识别相对异常的大挂单。"""
    if not levels:
        return None
    quantities = [float(level.get("quantity", 0) or 0) for level in levels]
    average = sum(quantities) / len(quantities)
    threshold = average * 1.8
    largest = max(levels, key=lambda level: float(level.get("quantity", 0) or 0))
    quantity = float(largest.get("quantity", 0) or 0)
    if quantity < threshold:
        return None
    price = float(largest.get("price", 0) or 0)
    distance = None
    if current_price:
        distance = abs(price - current_price) / current_price * 100
    return {
        "price": price,
        "price_text": largest.get("price_text", str(price)),
        "quantity": quantity,
        "quantity_text": largest.get("quantity_text", compact_quantity(quantity)),
        "distance_percent": distance,
    }


def analyze_orderbook(orderbook: dict[str, Any] | None, current_price: float | None = None) -> dict[str, Any]:
    """分析盘口强弱与关键挂单。"""
    if not orderbook:
        return {
            "status": "等待盘口数据",
            "buy_ratio": 0.0,
            "sell_ratio": 0.0,
            "bias": "等待数据",
            "bids": [],
            "asks": [],
            "large_bid": None,
            "large_ask": None,
            "support_level": None,
            "resistance_level": None,
        }
    bids = _with_cumulative(orderbook.get("bids", [])[:10])
    asks = _with_cumulative(orderbook.get("asks", [])[:10])
    bid_total = _sum_quantity(bids)
    ask_total = _sum_quantity(asks)
    total = bid_total + ask_total
    buy_ratio = bid_total / total * 100 if total else 0.0
    sell_ratio = ask_total / total * 100 if total else 0.0
    if buy_ratio >= 62:
        status = "买盘强势"
        bias = "多头占优"
    elif sell_ratio >= 62:
        status = "卖盘强势"
        bias = "空头占优"
    elif buy_ratio >= 55:
        status = "买盘吸筹"
        bias = "多头略占优"
    elif sell_ratio >= 55:
        status = "卖盘压制"
        bias = "空头略占优"
    else:
        status = "多空均衡"
        bias = "均衡"
    large_bid = _detect_large_order(bids, current_price)
    large_ask = _detect_large_order(asks, current_price)
    return {
        "status": status,
        "buy_ratio": buy_ratio,
        "sell_ratio": sell_ratio,
        "bias": bias,
        "bids": bids,
        "asks": asks,
        "large_bid": large_bid,
        "large_ask": large_ask,
        "support_level": bids[0]["price_text"] if bids else None,
        "resistance_level": asks[0]["price_text"] if asks else None,
    }
