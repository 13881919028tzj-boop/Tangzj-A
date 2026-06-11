"""后台 Binance 公共行情刷新服务。

刷新频率：
- 当前交易对象行情：1秒
- 当前交易对象 K线：1秒推动最新蜡烛，完整K线低频补齐
- 所有市场榜单与机会榜完整扫描：3秒
- 当前交易对象和顶部行情：1秒
- 全部 USDT 交易对象列表：启动时加载，后续每30分钟刷新一次

本模块不访问 Streamlit session_state，避免 SessionInfo/runtime 问题。
"""

from __future__ import annotations

import threading
import time

from services.binance_public import (
    get_all_24hr_tickers,
    get_24hr_ticker,
    get_exchange_info_symbols,
)
from services.kline_service import get_klines
from services.market_scanner import scan_market_opportunities
from services.fast_opportunity_engine import (
    get_fast_opportunity_settings,
    process_top1_fast_opportunity,
)
from services.auto_simulation_runner import run_auto_simulation_cycle
from services.live_auto_pilot import run_live_auto_trading_cycle
from services.watchlist_manager import sync_watchlist_from_rankings
from services.market_oi import get_derivatives_snapshot
from services.orderbook_service import get_orderbook
from services.whale_monitor import get_whale_snapshot
from services import market_cache


_STARTED = False
_START_LOCK = threading.Lock()


def _fallback_symbols(primary: str) -> list[str]:
    """当前币种异常时的安全降级顺序：当前 -> 机会榜TOP1 -> BTCUSDT。"""
    symbols: list[str] = []
    for symbol in [primary]:
        normalized = str(symbol or "").upper().strip()
        if normalized and normalized not in symbols:
            symbols.append(normalized)
    rankings = market_cache.get_rankings() or {}
    for key in ("trade_opportunities", "opportunities", "gainers", "volume"):
        for row in rankings.get(key, []) or []:
            normalized = str(row.get("symbol") or "").upper().strip()
            if normalized and normalized not in symbols:
                symbols.append(normalized)
                break
        if len(symbols) >= 2:
            break
    if "BTCUSDT" not in symbols:
        symbols.append("BTCUSDT")
    return symbols


def _refresh_symbols() -> list[str]:
    """刷新交易对象列表。"""
    symbols = get_exchange_info_symbols()
    market_cache.set_symbols(symbols)
    return symbols


def refresh_symbol_now(symbol: str) -> None:
    """立即刷新指定交易对象行情，失败时保留旧缓存。"""
    errors: list[str] = []
    for candidate in _fallback_symbols(symbol):
        try:
            ticker = get_24hr_ticker(candidate)
            market_cache.set_ticker(candidate, ticker)
            if candidate != str(symbol or "").upper().strip():
                market_cache.set_current_symbol(candidate)
                print(f"[AI模型8.5.1c] 当前交易对象行情异常，已降级切换 symbol={candidate}")
            return
        except Exception as exc:
            errors.append(f"{candidate}: {exc!r}")
    try:
        raise RuntimeError("; ".join(errors))
    except Exception as exc:
        message = f"Binance行情获取失败，请检查网络或稍后重试。symbol={symbol} error={repr(exc)}"
        print(f"[AI模型7.0.6] {message}")
        market_cache.set_error("Binance行情获取失败，请检查网络或稍后重试。")


def refresh_klines_now(symbol: str, interval: str) -> None:
    """立即刷新指定交易对象 K线，失败时保留旧缓存。"""
    try:
        rows = get_klines(symbol, interval, limit=300)
        market_cache.set_klines(symbol, interval, rows)
    except Exception as exc:
        message = f"K线数据获取失败，请检查网络或稍后重试。symbol={symbol} interval={interval} error={repr(exc)}"
        print(f"[AI模型7.0.6] {message}")
        market_cache.set_kline_error("K线数据获取失败，请检查网络或稍后重试。")


def refresh_orderbook_now(symbol: str) -> None:
    """立即刷新指定交易对象盘口，失败时保留旧缓存。"""
    try:
        orderbook = get_orderbook(symbol, limit=20)
        market_cache.set_orderbook(symbol, orderbook)
    except Exception as exc:
        message = f"盘口数据获取失败，请检查网络或稍后重试。symbol={symbol} error={repr(exc)}"
        print(f"[AI模型7.1.1] {message}")
        market_cache.set_orderbook_error("盘口数据获取失败，请检查网络或稍后重试。")


def refresh_derivatives_now(symbol: str) -> None:
    """立即刷新指定交易对象衍生品数据，失败时保留旧缓存。"""
    try:
        derivatives = get_derivatives_snapshot(symbol)
        market_cache.set_derivatives(symbol, derivatives)
    except Exception as exc:
        message = f"衍生品数据获取失败。symbol={symbol} error={repr(exc)}"
        print(f"[AI模型7.1.4] {message}")
        market_cache.set_derivatives_error("当前交易对象衍生品数据获取失败，正在重试。")


def refresh_whales_now(symbol: str) -> None:
    """立即刷新指定交易对象大单监控，失败时保留旧缓存。"""
    try:
        ticker = market_cache.get_ticker(symbol)
        derivatives = market_cache.get_derivatives(symbol)
        whales = get_whale_snapshot(symbol, ticker, derivatives)
        market_cache.set_whales(symbol, whales)
    except Exception as exc:
        message = f"大单数据获取失败。symbol={symbol} error={repr(exc)}"
        print(f"[AI模型7.1.6] {message}")
        market_cache.set_whale_error("大单数据获取失败，请稍后重试。")


def _refresh_rankings(symbols: list[str]) -> None:
    """刷新市场榜单。"""
    try:
        valid_symbols = set(symbols)
        tickers = get_all_24hr_tickers(valid_symbols)
        rankings = {
            "gainers": sorted(tickers, key=lambda item: item["price_change_percent"], reverse=True)[:10],
            "losers": sorted(tickers, key=lambda item: item["price_change_percent"])[:10],
            "volume": sorted(tickers, key=lambda item: item["quote_volume"], reverse=True)[:10],
        }
        rankings.update(scan_market_opportunities(tickers))
        market_cache.set_rankings(rankings)
        try:
            sync_watchlist_from_rankings(rankings)
        except Exception as watch_exc:
            print(f"[观察池] 后台同步失败，不影响行情榜单。error={repr(watch_exc)}")
        try:
            process_top1_fast_opportunity(rankings)
        except Exception as fast_exc:
            print(f"[AI模型8.3.1.1] TOP1快速捕捉失败，不影响主行情。error={repr(fast_exc)}")
        try:
            run_auto_simulation_cycle(rankings)
        except Exception as sim_exc:
            print(f"[AI模型8.5] 后台自动模拟循环失败，不影响行情刷新。error={repr(sim_exc)}")
        try:
            run_live_auto_trading_cycle(rankings)
        except Exception as live_auto_exc:
            print(f"[AI模型9.0] 后台自动交易循环失败，不影响行情刷新。error={repr(live_auto_exc)}")
    except Exception as exc:
        print(f"[AI模型7.0.6] Binance榜单获取失败 error={repr(exc)}")
        market_cache.set_error("Binance行情获取失败，请检查网络或稍后重试。")


def _worker() -> None:
    """后台交易对象列表、榜单和状态刷新循环。"""
    symbols = []
    last_symbol_refresh = 0.0
    last_ranking_refresh = 0.0
    while True:
        now = time.time()
        try:
            if not symbols or now - last_symbol_refresh >= 1800:
                symbols = _refresh_symbols()
                last_symbol_refresh = now

            market_cache.mark_status_ok()
            fast_settings = get_fast_opportunity_settings()
            ranking_seconds = max(3, int(fast_settings.get("TOP10_OPPORTUNITY_REFRESH_SECONDS", 3) or 3))
            if now - last_ranking_refresh >= ranking_seconds:
                _refresh_rankings(symbols)
                last_ranking_refresh = now
        except Exception as exc:
            print(f"[AI模型7.0.6] 后台刷新异常 error={repr(exc)}")
            market_cache.set_error("Binance行情获取失败，请检查网络或稍后重试。")
        time.sleep(1)


def _ticker_worker() -> None:
    """行情 ticker 独立刷新循环。"""
    while True:
        started_at = time.monotonic()
        try:
            current_symbol = market_cache.get_current_symbol()
            refresh_symbol_now(current_symbol)
            if market_cache.consume_refresh_request():
                refresh_symbol_now(market_cache.get_current_symbol())
        except Exception as exc:
            print(f"[AI模型7.1.2] 行情后台刷新异常 error={repr(exc)}")
            market_cache.set_error("Binance行情获取失败，请检查网络或稍后重试。")
        time.sleep(max(0.05, 1.0 - (time.monotonic() - started_at)))


def _kline_worker() -> None:
    """K线独立刷新循环。"""
    last_refresh = 0.0
    while True:
        started_at = time.monotonic()
        try:
            now = time.monotonic()
            if now - last_refresh >= 15:
                refresh_klines_now(market_cache.get_current_symbol(), market_cache.get_kline_interval())
                last_refresh = now
            if market_cache.consume_kline_refresh_request():
                refresh_klines_now(market_cache.get_current_symbol(), market_cache.get_kline_interval())
                last_refresh = time.monotonic()
        except Exception as exc:
            print(f"[AI模型7.1.2] K线后台刷新异常 error={repr(exc)}")
            market_cache.set_kline_error("K线数据获取失败，请检查网络或稍后重试。")
        time.sleep(max(0.05, 1.0 - (time.monotonic() - started_at)))


def _orderbook_worker() -> None:
    """盘口独立刷新循环，避免被K线和榜单请求拖慢。"""
    while True:
        started_at = time.monotonic()
        try:
            current_symbol = market_cache.get_current_symbol()
            refresh_orderbook_now(current_symbol)
            if market_cache.consume_orderbook_refresh_request():
                refresh_orderbook_now(market_cache.get_current_symbol())
        except Exception as exc:
            print(f"[AI模型7.1.1] 盘口后台刷新异常 error={repr(exc)}")
            market_cache.set_orderbook_error("盘口数据获取失败，请检查网络或稍后重试。")
        time.sleep(max(0.05, 1.0 - (time.monotonic() - started_at)))


def _derivatives_worker() -> None:
    """衍生品数据独立刷新循环。"""
    last_refresh = 0.0
    while True:
        started_at = time.monotonic()
        try:
            now = time.monotonic()
            if now - last_refresh >= 30:
                refresh_derivatives_now(market_cache.get_current_symbol())
                last_refresh = now
            if market_cache.consume_derivatives_refresh_request():
                refresh_derivatives_now(market_cache.get_current_symbol())
                last_refresh = time.monotonic()
        except Exception as exc:
            print(f"[AI模型7.1.4] 衍生品后台刷新异常 error={repr(exc)}")
            market_cache.set_derivatives_error("衍生品数据获取失败，正在重试。")
        time.sleep(max(0.05, 1.0 - (time.monotonic() - started_at)))


def _whale_worker() -> None:
    """大单监控独立刷新循环。"""
    while True:
        started_at = time.monotonic()
        try:
            current_symbol = market_cache.get_current_symbol()
            refresh_whales_now(current_symbol)
            if market_cache.consume_whale_refresh_request():
                refresh_whales_now(market_cache.get_current_symbol())
        except Exception as exc:
            print(f"[AI模型7.1.6] 大单后台刷新异常 error={repr(exc)}")
            market_cache.set_whale_error("大单数据获取失败，请稍后重试。")
        time.sleep(max(0.05, 5.0 - (time.monotonic() - started_at)))


def start_background_refresher() -> None:
    """启动后台刷新服务，只启动一次。"""
    global _STARTED
    with _START_LOCK:
        if _STARTED:
            return
        market_thread = threading.Thread(target=_worker, name="market-cache-refresher", daemon=True)
        ticker_thread = threading.Thread(target=_ticker_worker, name="ticker-cache-refresher", daemon=True)
        kline_thread = threading.Thread(target=_kline_worker, name="kline-cache-refresher", daemon=True)
        orderbook_thread = threading.Thread(target=_orderbook_worker, name="orderbook-cache-refresher", daemon=True)
        derivatives_thread = threading.Thread(target=_derivatives_worker, name="derivatives-cache-refresher", daemon=True)
        whale_thread = threading.Thread(target=_whale_worker, name="whale-cache-refresher", daemon=True)
        market_thread.start()
        ticker_thread.start()
        kline_thread.start()
        orderbook_thread.start()
        derivatives_thread.start()
        whale_thread.start()
        _STARTED = True
