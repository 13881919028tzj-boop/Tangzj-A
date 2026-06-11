"""Binance K线公共 REST 服务。

本模块只负责 Binance 现货公共 K线数据，不接入盘口、账户、交易、AI 或 WebSocket。
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from services.system_diagnostics import safe_binance_rest_get


BINANCE_PUBLIC_BASE_URL = "https://api.binance.com"
BINANCE_FUTURES_BASE_URL = "https://fapi.binance.com"
REQUEST_TIMEOUT = 10
SUPPORTED_INTERVALS = ("1m", "3m", "5m", "15m", "30m", "1h", "4h", "1d")


def _request_public(path: str, params: dict[str, Any] | None = None) -> Any:
    """请求 Binance 公共 REST 接口，失败时打印真实错误并抛出。"""
    try:
        return safe_binance_rest_get(path, params, base_url=BINANCE_PUBLIC_BASE_URL, timeout=REQUEST_TIMEOUT)
    except Exception as exc:
        print(f"[Binance K线] 请求失败 path={path} params={params} error={repr(exc)}")
        raise


def normalize_interval(interval: str) -> str:
    """校验并标准化 K线周期。"""
    normalized = str(interval or "1m").strip()
    return normalized if normalized in SUPPORTED_INTERVALS else "1m"


def get_klines(symbol: str, interval: str, limit: int = 300) -> list[dict[str, Any]]:
    """获取最近 K线数据。"""
    normalized_symbol = str(symbol or "").upper().strip()
    normalized_interval = normalize_interval(interval)
    params = {
        "symbol": normalized_symbol,
        "interval": normalized_interval,
        "limit": max(20, min(int(limit), 1000)),
    }
    try:
        raw_rows = _request_public("/api/v3/klines", params)
        data_source = "spot_rest"
    except Exception as spot_exc:
        try:
            raw_rows = safe_binance_rest_get(
                "/fapi/v1/klines",
                params,
                base_url=BINANCE_FUTURES_BASE_URL,
                fallback_base_url=None,
                timeout=REQUEST_TIMEOUT,
            )
            data_source = "futures_rest_fallback"
        except Exception as futures_exc:
            raise RuntimeError(f"K线REST获取失败 symbol={normalized_symbol} spot={spot_exc!r} futures={futures_exc!r}") from futures_exc
    rows: list[dict[str, Any]] = []
    for row in raw_rows:
        open_time = int(row[0])
        close_time = int(row[6])
        rows.append(
            {
                "open_time": open_time,
                "open_datetime": datetime.fromtimestamp(open_time / 1000),
                "close_time": close_time,
                "close_datetime": datetime.fromtimestamp(close_time / 1000),
                "open": float(row[1]),
                "high": float(row[2]),
                "low": float(row[3]),
                "close": float(row[4]),
                "volume": float(row[5]),
                "data_source": data_source,
            }
        )
    return rows
