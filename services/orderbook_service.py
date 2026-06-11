"""Binance 盘口订单簿公共 REST 服务。

本模块只读取现货公共深度接口，不接入账户、交易、AI 或 WebSocket。
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Any

from services.system_diagnostics import safe_binance_rest_get


BINANCE_PUBLIC_BASE_URL = "https://api.binance.com"
REQUEST_TIMEOUT = 3


def _request_public(path: str, params: dict[str, Any] | None = None) -> Any:
    """请求 Binance 公共 REST 接口，失败时打印真实错误并抛出。"""
    try:
        return safe_binance_rest_get(path, params, base_url=BINANCE_PUBLIC_BASE_URL, timeout=REQUEST_TIMEOUT)
    except Exception as exc:
        print(f"[Binance盘口] 请求失败 path={path} params={params} error={repr(exc)}")
        raise


def _normalize_level(level: list[str]) -> dict[str, Any]:
    """标准化单档盘口，保留原始精度字符串。"""
    price_text = str(level[0])
    quantity_text = str(level[1])
    return {
        "price": float(price_text),
        "quantity": float(quantity_text),
        "price_text": price_text.rstrip("0").rstrip("."),
        "quantity_text": quantity_text.rstrip("0").rstrip("."),
    }


def _with_cumulative(levels: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """补齐累计数量，供页面和 API 统一显示。"""
    total = Decimal("0")
    result: list[dict[str, Any]] = []
    for level in levels:
        total += Decimal(str(level.get("quantity", 0)))
        item = dict(level)
        item["cumulative"] = float(total)
        item["cumulative_text"] = str(total.normalize()).rstrip("0").rstrip(".")
        result.append(item)
    return result


def get_orderbook(symbol: str, limit: int = 20) -> dict[str, Any]:
    """获取指定交易对象盘口订单簿。"""
    normalized_symbol = str(symbol or "").upper().strip()
    data = _request_public("/api/v3/depth", {"symbol": normalized_symbol, "limit": max(5, min(limit, 100))})
    bids = _with_cumulative([_normalize_level(level) for level in data.get("bids", [])[:10]])
    asks = _with_cumulative([_normalize_level(level) for level in data.get("asks", [])[:10]])
    if not bids or not asks:
        raise RuntimeError(f"盘口数据为空 symbol={normalized_symbol} bids={len(bids)} asks={len(asks)}")
    return {
        "symbol": normalized_symbol,
        "last_update_id": data.get("lastUpdateId"),
        "lastUpdateId": data.get("lastUpdateId"),
        "bids": bids,
        "asks": asks,
        "updated_at": datetime.now().strftime("%H:%M:%S"),
        "status": "正常",
        "error": "",
    }


def decimal_sum(levels: list[dict[str, Any]]) -> Decimal:
    """高精度累加数量。"""
    total = Decimal("0")
    for level in levels:
        total += Decimal(str(level.get("quantity", 0)))
    return total
