"""线程安全行情缓存。

本模块只保存 Binance 公共行情与 K线缓存，不访问 Streamlit session_state。
"""

from __future__ import annotations

import threading
from copy import deepcopy
from datetime import datetime
from typing import Any


_LOCK = threading.RLock()

_CACHE: dict[str, Any] = {
    "current_symbol": "BTCUSDT",
    "kline_interval": "1m",
    "symbols": [],
    "tickers": {},
    "klines": {},
    "orderbooks": {},
    "derivatives": {},
    "whales": {},
    "rankings": None,
    "binance_status": "初始化",
    "kline_status": "初始化中",
    "orderbook_status": "初始化中",
    "derivatives_status": "初始化中",
    "whale_status": "初始化中",
    "last_update_time": "初始化中",
    "kline_last_update_time": "初始化中",
    "orderbook_last_update_time": "初始化中",
    "derivatives_last_update_time": "初始化中",
    "whale_last_update_time": "初始化中",
    "last_error": "",
    "kline_last_error": "",
    "orderbook_last_error": "",
    "derivatives_last_error": "",
    "whale_last_error": "",
    "refresh_counts": {"ticker": 0, "rankings": 0, "symbols": 0, "status": 0, "klines": 0, "orderbook": 0, "derivatives": 0, "whale": 0},
    "manual_refresh_requested": False,
    "kline_refresh_requested": False,
    "orderbook_refresh_requested": False,
    "derivatives_refresh_requested": False,
    "whale_refresh_requested": False,
}


def now_text() -> str:
    """返回统一时间格式。"""
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def set_current_symbol(symbol: str) -> None:
    """设置全局当前交易对象。"""
    normalized = str(symbol or "").upper().strip()
    if not normalized:
        return
    with _LOCK:
        changed = normalized != _CACHE["current_symbol"]
        _CACHE["current_symbol"] = normalized
        if changed:
            _CACHE["kline_status"] = "正在获取"
            _CACHE["orderbook_status"] = "正在获取"
            _CACHE["derivatives_status"] = "正在获取"
            _CACHE["whale_status"] = "正在获取"
            _CACHE["kline_last_update_time"] = "初始化中"
            _CACHE["orderbook_last_update_time"] = "初始化中"
            _CACHE["derivatives_last_update_time"] = "初始化中"
            _CACHE["whale_last_update_time"] = "初始化中"
            _CACHE["kline_last_error"] = ""
            _CACHE["orderbook_last_error"] = ""
            _CACHE["derivatives_last_error"] = ""
            _CACHE["whale_last_error"] = ""
        _CACHE["manual_refresh_requested"] = True
        _CACHE["kline_refresh_requested"] = True
        _CACHE["orderbook_refresh_requested"] = True
        _CACHE["derivatives_refresh_requested"] = True
        _CACHE["whale_refresh_requested"] = True


def get_current_symbol() -> str:
    """读取全局当前交易对象。"""
    with _LOCK:
        return str(_CACHE["current_symbol"])


def set_kline_interval(interval: str) -> None:
    """设置全局 K线周期。"""
    normalized = str(interval or "1m").strip()
    if not normalized:
        return
    with _LOCK:
        _CACHE["kline_interval"] = normalized
        _CACHE["kline_refresh_requested"] = True


def get_kline_interval() -> str:
    """读取全局 K线周期。"""
    with _LOCK:
        return str(_CACHE["kline_interval"])


def set_symbols(symbols: list[str]) -> None:
    """写入全部交易对象列表。"""
    with _LOCK:
        if symbols:
            _CACHE["symbols"] = list(symbols)
            _CACHE["refresh_counts"]["symbols"] += 1


def get_symbols(fallback: list[str]) -> list[str]:
    """读取交易对象列表。"""
    with _LOCK:
        return list(_CACHE["symbols"] or fallback)


def set_ticker(symbol: str, ticker: dict[str, Any]) -> None:
    """写入单个交易对象行情。"""
    normalized = str(symbol or "").upper().strip()
    with _LOCK:
        _CACHE["tickers"][normalized] = deepcopy(ticker)
        price = ticker.get("last_price")
        if price is not None:
            _update_cached_latest_klines_locked(normalized, float(price))
        _CACHE["last_update_time"] = now_text()
        _CACHE["binance_status"] = "在线"
        _CACHE["last_error"] = ""
        _CACHE["refresh_counts"]["ticker"] += 1


def get_ticker(symbol: str) -> dict[str, Any] | None:
    """读取单个交易对象行情。"""
    with _LOCK:
        ticker = _CACHE["tickers"].get(symbol)
        return deepcopy(ticker) if ticker else None


def _kline_key(symbol: str, interval: str) -> str:
    """生成 K线缓存键。"""
    return f"{str(symbol).upper().strip()}|{str(interval).strip()}"


def _update_cached_latest_klines_locked(symbol: str, latest_price: float) -> None:
    """用最新 ticker 价格推动当前未收盘蜡烛实时变化。"""
    prefix = f"{str(symbol).upper().strip()}|"
    updated = False
    for key, rows in list(_CACHE["klines"].items()):
        if not key.startswith(prefix) or not rows:
            continue
        latest = rows[-1]
        latest["close"] = latest_price
        latest["high"] = max(float(latest.get("high", latest_price) or latest_price), latest_price)
        latest["low"] = min(float(latest.get("low", latest_price) or latest_price), latest_price)
        updated = True
    if updated:
        _CACHE["kline_status"] = "实时"
        _CACHE["kline_last_update_time"] = now_text()
        _CACHE["kline_last_error"] = ""


def set_klines(symbol: str, interval: str, rows: list[dict[str, Any]]) -> None:
    """写入指定交易对象与周期的 K线缓存。"""
    if not rows:
        return
    with _LOCK:
        _CACHE["klines"][_kline_key(symbol, interval)] = deepcopy(rows)
        _CACHE["kline_status"] = "实时"
        _CACHE["kline_last_update_time"] = now_text()
        _CACHE["kline_last_error"] = ""
        _CACHE["refresh_counts"]["klines"] += 1


def get_klines(symbol: str, interval: str) -> list[dict[str, Any]]:
    """读取指定交易对象与周期的 K线缓存。"""
    with _LOCK:
        rows = _CACHE["klines"].get(_kline_key(symbol, interval), [])
        return deepcopy(rows)


def set_orderbook(symbol: str, orderbook: dict[str, Any]) -> None:
    """写入指定交易对象盘口缓存。"""
    if not orderbook:
        return
    normalized = str(symbol or "").upper().strip()
    with _LOCK:
        _CACHE["orderbooks"][normalized] = deepcopy(orderbook)
        _CACHE["orderbook_status"] = "实时"
        _CACHE["orderbook_last_update_time"] = now_text()
        _CACHE["orderbook_last_error"] = ""
        _CACHE["refresh_counts"]["orderbook"] += 1


def get_orderbook(symbol: str) -> dict[str, Any] | None:
    """读取指定交易对象盘口缓存。"""
    normalized = str(symbol or "").upper().strip()
    with _LOCK:
        orderbook = _CACHE["orderbooks"].get(normalized)
        return deepcopy(orderbook) if orderbook else None


def set_derivatives(symbol: str, derivatives: dict[str, Any]) -> None:
    """写入指定交易对象衍生品缓存。"""
    if not derivatives:
        return
    normalized = str(symbol or "").upper().strip()
    with _LOCK:
        _CACHE["derivatives"][normalized] = deepcopy(derivatives)
        _CACHE["derivatives_status"] = "实时"
        _CACHE["derivatives_last_update_time"] = now_text()
        _CACHE["derivatives_last_error"] = ""
        _CACHE["refresh_counts"]["derivatives"] += 1


def get_derivatives(symbol: str) -> dict[str, Any] | None:
    """读取指定交易对象衍生品缓存。"""
    normalized = str(symbol or "").upper().strip()
    with _LOCK:
        derivatives = _CACHE["derivatives"].get(normalized)
        return deepcopy(derivatives) if derivatives else None


def set_whales(symbol: str, whales: dict[str, Any]) -> None:
    """写入指定交易对象大单监控缓存。"""
    if not whales:
        return
    normalized = str(symbol or "").upper().strip()
    with _LOCK:
        _CACHE["whales"][normalized] = deepcopy(whales)
        _CACHE["whale_status"] = "实时"
        _CACHE["whale_last_update_time"] = now_text()
        _CACHE["whale_last_error"] = ""
        _CACHE["refresh_counts"]["whale"] += 1


def get_whales(symbol: str) -> dict[str, Any] | None:
    """读取指定交易对象大单监控缓存。"""
    normalized = str(symbol or "").upper().strip()
    with _LOCK:
        whales = _CACHE["whales"].get(normalized)
        return deepcopy(whales) if whales else None


def set_rankings(rankings: dict[str, list[dict[str, Any]]]) -> None:
    """写入市场排行榜缓存。"""
    with _LOCK:
        _CACHE["rankings"] = deepcopy(rankings)
        _CACHE["last_update_time"] = now_text()
        _CACHE["binance_status"] = "在线"
        _CACHE["last_error"] = ""
        _CACHE["refresh_counts"]["rankings"] += 1


def get_rankings() -> dict[str, list[dict[str, Any]]] | None:
    """读取市场排行榜缓存。"""
    with _LOCK:
        return deepcopy(_CACHE["rankings"]) if _CACHE["rankings"] else None


def set_error(message: str) -> None:
    """记录最近错误，但保留上一条成功行情数据。"""
    with _LOCK:
        _CACHE["binance_status"] = "异常"
        _CACHE["last_error"] = message


def set_kline_error(message: str) -> None:
    """记录 K线错误，但保留上一条成功 K线数据。"""
    with _LOCK:
        _CACHE["kline_status"] = "延迟"
        _CACHE["kline_last_error"] = message


def set_orderbook_error(message: str) -> None:
    """记录盘口错误，但保留上一条成功盘口数据。"""
    with _LOCK:
        _CACHE["orderbook_status"] = "延迟"
        _CACHE["orderbook_last_error"] = message


def set_derivatives_error(message: str) -> None:
    """记录衍生品错误，但保留上一条成功衍生品数据。"""
    with _LOCK:
        _CACHE["derivatives_status"] = "延迟"
        _CACHE["derivatives_last_error"] = message


def set_whale_error(message: str) -> None:
    """记录大单监控错误，但保留上一条成功大单数据。"""
    with _LOCK:
        _CACHE["whale_status"] = "延迟"
        _CACHE["whale_last_error"] = message


def mark_status_ok() -> None:
    """记录连接状态正常。"""
    with _LOCK:
        _CACHE["binance_status"] = "在线"
        _CACHE["refresh_counts"]["status"] += 1


def request_refresh() -> None:
    """请求后台尽快刷新当前交易对象。"""
    with _LOCK:
        _CACHE["manual_refresh_requested"] = True
        _CACHE["kline_refresh_requested"] = True
        _CACHE["orderbook_refresh_requested"] = True
        _CACHE["derivatives_refresh_requested"] = True
        _CACHE["whale_refresh_requested"] = True


def consume_refresh_request() -> bool:
    """读取并清空手动刷新请求。"""
    with _LOCK:
        requested = bool(_CACHE["manual_refresh_requested"])
        _CACHE["manual_refresh_requested"] = False
        return requested


def request_kline_refresh() -> None:
    """请求后台尽快刷新 K线。"""
    with _LOCK:
        _CACHE["kline_refresh_requested"] = True


def request_orderbook_refresh() -> None:
    """请求后台尽快刷新盘口。"""
    with _LOCK:
        _CACHE["orderbook_refresh_requested"] = True


def request_derivatives_refresh() -> None:
    """请求后台尽快刷新衍生品数据。"""
    with _LOCK:
        _CACHE["derivatives_refresh_requested"] = True


def request_whale_refresh() -> None:
    """请求后台尽快刷新大单监控。"""
    with _LOCK:
        _CACHE["whale_refresh_requested"] = True


def consume_kline_refresh_request() -> bool:
    """读取并清空 K线刷新请求。"""
    with _LOCK:
        requested = bool(_CACHE["kline_refresh_requested"])
        _CACHE["kline_refresh_requested"] = False
        return requested


def consume_orderbook_refresh_request() -> bool:
    """读取并清空盘口刷新请求。"""
    with _LOCK:
        requested = bool(_CACHE["orderbook_refresh_requested"])
        _CACHE["orderbook_refresh_requested"] = False
        return requested


def consume_derivatives_refresh_request() -> bool:
    """读取并清空衍生品刷新请求。"""
    with _LOCK:
        requested = bool(_CACHE["derivatives_refresh_requested"])
        _CACHE["derivatives_refresh_requested"] = False
        return requested


def consume_whale_refresh_request() -> bool:
    """读取并清空大单刷新请求。"""
    with _LOCK:
        requested = bool(_CACHE["whale_refresh_requested"])
        _CACHE["whale_refresh_requested"] = False
        return requested


def snapshot() -> dict[str, Any]:
    """返回缓存快照，供页面读取。"""
    with _LOCK:
        return deepcopy(_CACHE)
