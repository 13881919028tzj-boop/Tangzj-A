"""本地前端行情 API。

浏览器组件不能稳定直连 Binance 公共接口，因此由本地轻量 HTTP 服务读取
market_cache，再提供给前端 K线、盘口和顶部行情组件。
"""

from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib.parse import parse_qs, urlparse

from services import market_cache
from services.binance_public import get_24hr_ticker
from services.kline_service import get_klines
from services.orderbook_service import get_orderbook
from services.whale_monitor import get_whale_snapshot


_START_LOCK = threading.Lock()
_STARTED = False
_PORT = 8765


def _json_default(value: Any) -> Any:
    if hasattr(value, "isoformat"):
        return value.isoformat()
    return str(value)


def _kline_payload(symbol: str, interval: str) -> list[dict[str, Any]]:
    rows = market_cache.get_klines(symbol, interval)
    if not rows:
        market_cache.request_kline_refresh()
        try:
            rows = get_klines(symbol, interval, limit=300)
            market_cache.set_klines(symbol, interval, rows)
        except Exception as exc:
            market_cache.set_kline_error(f"K线REST兜底失败：{exc!r}")
            rows = []
    result: list[dict[str, Any]] = []
    for row in rows:
        result.append(
            {
                "openTime": row.get("open_time"),
                "closeTime": row.get("close_time"),
                "open": row.get("open"),
                "high": row.get("high"),
                "low": row.get("low"),
                "close": row.get("close"),
                "volume": row.get("volume"),
            }
        )
    return result


def _ticker_payload(symbol: str) -> dict[str, Any]:
    ticker = market_cache.get_ticker(symbol)
    source = "market_cache"
    if not ticker:
        try:
            ticker = get_24hr_ticker(symbol)
            market_cache.set_ticker(symbol, ticker)
            source = "binance_rest_fallback"
        except Exception as exc:
            market_cache.set_error(f"Ticker REST回退失败：{exc!r}")
            return {
                "ok": False,
                "error": "ticker_not_found",
                "message": f"{symbol} 实时价格暂未写入缓存",
                "symbol": symbol,
                "last_price": None,
                "price": None,
                "price_change_percent": None,
                "change_24h": None,
                "updated_at": "",
                "source": source,
                "detail": f"Ticker REST回退失败：{exc!r}",
            }
    price = ticker.get("last_price")
    change = ticker.get("price_change_percent")
    return {
        "ok": True,
        "symbol": str(ticker.get("symbol") or symbol).upper(),
        "price": price,
        "last_price": price,
        "change_24h": change,
        "price_change_percent": change,
        "updated_at": ticker.get("updated_at") or ticker.get("close_time") or "",
        "source": source,
        **ticker,
    }


def _klines_response(symbol: str, interval: str) -> dict[str, Any]:
    rows = _kline_payload(symbol, interval)
    if not rows:
        snapshot = market_cache.snapshot()
        message = snapshot.get("kline_last_error") or f"{symbol} {interval} K线正在后台刷新"
        return {
            "ok": True,
            "error": "klines_not_found",
            "message": message,
            "symbol": symbol,
            "interval": interval,
            "rows": [],
            "source": "market_cache",
        }
    return {
        "ok": True,
        "symbol": symbol,
        "interval": interval,
        "rows": rows,
        "source": "market_cache",
    }


def _orderbook_response(symbol: str) -> dict[str, Any]:
    try:
        orderbook = get_orderbook(symbol, limit=20)
        market_cache.set_orderbook(symbol, orderbook)
        return {"ok": True, "source": "local_api_rest_live", **orderbook}
    except Exception as exc:
        market_cache.set_orderbook_error(f"盘口REST兜底失败：{exc!r}")
    orderbook = market_cache.get_orderbook(symbol)
    if orderbook:
        return {"ok": True, "source": "market_cache_stale_fallback", **orderbook}
    market_cache.request_orderbook_refresh()
    snapshot = market_cache.snapshot()
    return {
        "ok": True,
        "symbol": symbol,
        "bids": [],
        "asks": [],
        "status": "正在获取",
        "message": snapshot.get("orderbook_last_error") or f"{symbol} 盘口正在后台刷新",
        "updated_at": snapshot.get("orderbook_last_update_time", "初始化中"),
        "source": "market_cache",
    }


def _empty_whale_stats() -> dict[str, dict[str, Any]]:
    return {
        "1m": {"count": 0, "buy_count": 0, "sell_count": 0, "buy_amount": 0.0, "sell_amount": 0.0, "net_amount": 0.0, "trade_count": 0},
        "5m": {"count": 0, "buy_count": 0, "sell_count": 0, "buy_amount": 0.0, "sell_amount": 0.0, "net_amount": 0.0, "trade_count": 0},
        "15m": {"count": 0, "buy_count": 0, "sell_count": 0, "buy_amount": 0.0, "sell_amount": 0.0, "net_amount": 0.0, "trade_count": 0},
    }


def _whales_response(symbol: str) -> dict[str, Any]:
    try:
        ticker = market_cache.get_ticker(symbol) or get_24hr_ticker(symbol)
        derivatives = market_cache.get_derivatives(symbol)
        whales = get_whale_snapshot(symbol, ticker, derivatives)
        market_cache.set_whales(symbol, whales)
        return {"ok": True, "source": "local_api_rest_live", **whales}
    except Exception as exc:
        market_cache.set_whale_error(f"大单REST兜底失败：{exc!r}")
    whales = market_cache.get_whales(symbol)
    if whales:
        return {"ok": True, "source": "market_cache_stale_fallback", **whales}
    market_cache.request_whale_refresh()
    snapshot = market_cache.snapshot()
    message = snapshot.get("whale_last_error") or f"{symbol} 大单正在后台刷新"
    return {
        "ok": True,
        "symbol": symbol,
        "updated_time": snapshot.get("whale_last_update_time", "初始化中"),
        "updated_at": snapshot.get("whale_last_update_time", "初始化中"),
        "whale_score": 0,
        "whale_score_text": "等待数据",
        "whale_direction": "正在获取大单数据",
        "dealer_behavior": "等待数据",
        "risk_tip": message,
        "explanation": message,
        "net_inflow_5m": 0,
        "net_inflow_15m": 0,
        "active_buy_amount": 0,
        "active_sell_amount": 0,
        "largest_buy_order": {},
        "largest_sell_order": {},
        "buy_whale_count": 0,
        "sell_whale_count": 0,
        "buy_sell_count_text": "买入 0 笔 / 卖出 0 笔",
        "buy_sell_ratio": 0,
        "latest": [],
        "recent_trades": [],
        "stats": _empty_whale_stats(),
        "data_quality": "pending",
        "source": "market_cache",
        "debug": {
            "symbol": symbol,
            "data_source": "后台刷新中",
            "raw_trade_count": 0,
            "threshold": 0,
            "stats_5m_trade_count": 0,
            "stats_15m_trade_count": 0,
            "active_buy_amount": 0,
            "active_sell_amount": 0,
            "buy_whale_count": 0,
            "sell_whale_count": 0,
            "data_quality": "pending",
            "error": message,
        },
    }


class _Handler(BaseHTTPRequestHandler):
    server_version = "AIModelLocalAPI/7.1.2"

    def log_message(self, format: str, *args: Any) -> None:  # noqa: A002
        return

    def _send_json(self, payload: Any, status: int = 200) -> None:
        body = json.dumps(payload, ensure_ascii=False, default=_json_default).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_OPTIONS(self) -> None:
        self._send_json({"ok": True})

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        params = parse_qs(parsed.query)
        symbol = str(params.get("symbol", [market_cache.get_current_symbol()])[0] or "").upper().strip()
        interval = str(params.get("interval", [market_cache.get_kline_interval()])[0] or "1m")
        try:
            if parsed.path == "/api/ticker":
                self._send_json(_ticker_payload(symbol))
            elif parsed.path == "/api/klines":
                self._send_json(_klines_response(symbol, interval))
            elif parsed.path == "/api/orderbook":
                self._send_json(_orderbook_response(symbol))
            elif parsed.path == "/api/whales":
                self._send_json(_whales_response(symbol))
            elif parsed.path == "/api/snapshot":
                self._send_json(market_cache.snapshot())
            else:
                self._send_json({"ok": False, "error": "not_found", "message": "接口不存在"}, status=404)
        except Exception as exc:
            self._send_json({"ok": False, "error": "internal_error", "message": f"本地行情API异常：{exc!r}"}, status=500)


def start_local_api_server() -> int:
    """启动本地 HTTP API。端口占用时自动尝试后续端口。"""
    global _STARTED, _PORT
    with _START_LOCK:
        if _STARTED:
            return _PORT
        last_error: Exception | None = None
        host = "0.0.0.0"
        for port in range(8765, 8775):
            try:
                server = ThreadingHTTPServer((host, port), _Handler)
                thread = threading.Thread(target=server.serve_forever, name=f"ai-model-local-api-{port}", daemon=True)
                thread.start()
                _PORT = port
                _STARTED = True
                print(f"[AI模型7.1.2] 前端行情API已启动 host={host} port={port}")
                return _PORT
            except OSError as exc:
                last_error = exc
                continue
        raise RuntimeError(f"本地前端行情API启动失败: {last_error!r}")


def get_local_api_port() -> int:
    """读取本地 HTTP API 端口。"""
    return _PORT
