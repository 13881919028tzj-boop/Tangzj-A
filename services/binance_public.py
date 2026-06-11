"""Binance 公共行情 REST 服务。

当前 7.0.2 只允许接入 Binance 公共行情：
- 单币种 24h 行情
- 全市场 24h 行情
- 涨幅榜 / 跌幅榜 / 成交量榜

禁止在本文件中加入 K线、盘口、AI、交易、账户 API 或 WebSocket。
"""

from __future__ import annotations

from typing import Any

from services.system_diagnostics import safe_binance_rest_get


BINANCE_PUBLIC_BASE_URL = "https://api.binance.com"
REQUEST_TIMEOUT = 10
EXCLUDED_KEYWORDS = ("UP", "DOWN", "BULL", "BEAR")


def _request_public(path: str, params: dict[str, Any] | None = None) -> Any:
    """请求 Binance 公共 REST 接口，失败时打印真实错误并抛出。"""
    try:
        return safe_binance_rest_get(path, params, base_url=BINANCE_PUBLIC_BASE_URL, timeout=REQUEST_TIMEOUT)
    except Exception as exc:
        print(f"[Binance公共行情] 请求失败 path={path} params={params} error={repr(exc)}")
        raise


def is_valid_usdt_symbol(symbol: str) -> bool:
    """过滤 USDT 交易对象，排除明显杠杆或异常交易对象。"""
    normalized = str(symbol or "").upper().strip()
    if not normalized.endswith("USDT"):
        return False
    base = normalized[:-4]
    return not any(keyword in base for keyword in EXCLUDED_KEYWORDS)


def get_exchange_info_symbols() -> list[str]:
    """从 Binance ExchangeInfo 同步全部可交易 USDT 现货交易对象。"""
    data = _request_public("/api/v3/exchangeInfo")
    symbols = []
    for item in data.get("symbols", []):
        symbol = item.get("symbol", "")
        if (
            item.get("status") == "TRADING"
            and item.get("quoteAsset") == "USDT"
            and item.get("isSpotTradingAllowed", True)
            and is_valid_usdt_symbol(symbol)
        ):
            symbols.append(symbol)
    priority = ["BTCUSDT", "ETHUSDT", "BNBUSDT", "SOLUSDT", "DOGEUSDT", "XRPUSDT"]
    ordered = [symbol for symbol in priority if symbol in symbols]
    ordered.extend(sorted(symbol for symbol in symbols if symbol not in ordered))
    return ordered


def normalize_ticker(raw: dict[str, Any]) -> dict[str, Any]:
    """标准化 Binance 24h ticker 返回字段，方便页面展示。"""
    return {
        "symbol": raw.get("symbol", ""),
        "last_price": float(raw.get("lastPrice", 0) or 0),
        "price_change_percent": float(raw.get("priceChangePercent", 0) or 0),
        "high_price": float(raw.get("highPrice", 0) or 0),
        "low_price": float(raw.get("lowPrice", 0) or 0),
        "quote_volume": float(raw.get("quoteVolume", 0) or 0),
    }


def get_24hr_ticker(symbol: str) -> dict[str, Any]:
    """返回单个交易对象的 24h 行情。"""
    normalized = str(symbol or "").upper().strip()
    data = _request_public("/api/v3/ticker/24hr", {"symbol": normalized})
    return normalize_ticker(data)


def get_all_24hr_tickers(valid_symbols: set[str] | None = None) -> list[dict[str, Any]]:
    """返回全市场 24h 行情，只保留有效 USDT 交易对象。"""
    data = _request_public("/api/v3/ticker/24hr")
    if not isinstance(data, list):
        return []
    result = []
    for item in data:
        symbol = item.get("symbol", "")
        if not is_valid_usdt_symbol(symbol):
            continue
        if valid_symbols is not None and symbol not in valid_symbols:
            continue
        result.append(normalize_ticker(item))
    return result


def get_top_gainers(limit: int = 10, valid_symbols: set[str] | None = None) -> list[dict[str, Any]]:
    """返回涨幅榜。"""
    tickers = get_all_24hr_tickers(valid_symbols)
    return sorted(tickers, key=lambda item: item["price_change_percent"], reverse=True)[:limit]


def get_top_losers(limit: int = 10, valid_symbols: set[str] | None = None) -> list[dict[str, Any]]:
    """返回跌幅榜。"""
    tickers = get_all_24hr_tickers(valid_symbols)
    return sorted(tickers, key=lambda item: item["price_change_percent"])[:limit]


def get_top_volume(limit: int = 10, valid_symbols: set[str] | None = None) -> list[dict[str, Any]]:
    """返回成交量榜，按 24h USDT 成交额排序。"""
    tickers = get_all_24hr_tickers(valid_symbols)
    return sorted(tickers, key=lambda item: item["quote_volume"], reverse=True)[:limit]
