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
import fcntl
import os
from pathlib import Path
from typing import Any, Callable

from services.binance_public import (
    get_all_24hr_tickers,
    get_24hr_ticker,
    get_exchange_info_symbols,
    normalize_ticker,
)
from services.kline_service import get_klines
from services.market_scanner import scan_market_opportunities
from services.fast_opportunity_engine import (
    collect_top10_opportunities,
    get_fast_opportunity_settings,
    get_fast_opportunity_status,
    process_top1_fast_opportunity,
)
from services.auto_simulation_runner import run_auto_simulation_cycle
from services.grid_trade_engine import load_grid_bots, update_grid_bots
from services.live_auto_pilot import run_live_auto_trading_cycle
from services.watchlist_manager import sync_watchlist_from_rankings
from services.market_oi import get_derivatives_snapshot
from services.orderbook_service import get_orderbook
from services.whale_monitor import get_whale_snapshot
from services import market_cache


_STARTED = False
_START_LOCK = threading.Lock()
_THREADS: dict[str, threading.Thread] = {}
_LOCK_HANDLE: Any | None = None
LOG_DIR = Path(__file__).resolve().parents[1] / "logs"
LIVE_CACHE_LOG = LOG_DIR / "live_cache_debug.log"
REFRESHER_LOCK = LOG_DIR / "background_refresher.lock"
MAX_PRIORITY_REFRESH_PER_LOOP = 4


def _debug_log(path: Path, message: str) -> None:
    """Append a short runtime diagnostic line without secrets."""
    try:
        LOG_DIR.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as handle:
            handle.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')} {message}\n")
    except Exception:
        pass


def _acquire_process_lock_locked() -> bool:
    """Ensure only one process owns the background trading refresh loop."""
    global _LOCK_HANDLE
    if _LOCK_HANDLE is not None:
        return True
    try:
        LOG_DIR.mkdir(parents=True, exist_ok=True)
        handle = REFRESHER_LOCK.open("a+", encoding="utf-8")
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        handle.seek(0)
        handle.truncate()
        handle.write(f"pid={os.getpid()} started={time.strftime('%Y-%m-%d %H:%M:%S')}\n")
        handle.flush()
        _LOCK_HANDLE = handle
        return True
    except BlockingIOError:
        _debug_log(LIVE_CACHE_LOG, "background_refresher_already_locked")
        return False
    except Exception as exc:
        _debug_log(LIVE_CACHE_LOG, f"background_refresher_lock_failed error={repr(exc)}")
        return True


def _start_thread_locked(name: str, target: Callable[[], None]) -> bool:
    existing = _THREADS.get(name)
    if existing and existing.is_alive():
        return False
    thread = threading.Thread(target=target, name=name, daemon=True)
    thread.start()
    _THREADS[name] = thread
    _debug_log(LIVE_CACHE_LOG, f"background_thread_started name={name}")
    return True


def _standard_ticker(row: dict[str, Any]) -> dict[str, Any]:
    """Normalize ranking/public ticker rows before writing the ticker cache."""
    ticker = normalize_ticker(row)
    if not ticker.get("symbol"):
        ticker["symbol"] = str(row.get("symbol") or "").upper().strip()
    return ticker


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
        synced = 0
        for ticker_row in tickers:
            symbol = str(ticker_row.get("symbol") or "").upper().strip()
            if not symbol:
                continue
            market_cache.set_ticker(symbol, _standard_ticker(ticker_row))
            synced += 1
        rankings = {
            "gainers": sorted(tickers, key=lambda item: item["price_change_percent"], reverse=True)[:10],
            "losers": sorted(tickers, key=lambda item: item["price_change_percent"])[:10],
            "volume": sorted(tickers, key=lambda item: item["quote_volume"], reverse=True)[:10],
        }
        rankings.update(scan_market_opportunities(tickers))
        market_cache.set_rankings(rankings)
        _debug_log(LIVE_CACHE_LOG, f"rankings_refresh tickers={len(tickers)} ticker_cache_synced={synced}")
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
            grid_prices = {}
            for bot in load_grid_bots():
                if bot.get("status") != "running":
                    continue
                symbol = str(bot.get("symbol") or "").upper().strip()
                ticker = market_cache.get_ticker(symbol) or {}
                price = float(ticker.get("last_price") or 0)
                if symbol and price > 0:
                    grid_prices[symbol] = price
            if grid_prices:
                update_grid_bots(grid_prices)
        except Exception as grid_exc:
            print(f"[网格交易] 后台网格模拟循环失败，不影响行情刷新。error={repr(grid_exc)}")
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
            ranking_seconds = max(60, int(fast_settings.get("TOP10_OPPORTUNITY_REFRESH_SECONDS", 60) or 60))
            if now - last_ranking_refresh >= ranking_seconds:
                _refresh_rankings(symbols)
                last_ranking_refresh = now
        except Exception as exc:
            print(f"[AI模型7.0.6] 后台刷新异常 error={repr(exc)}")
            market_cache.set_error("Binance行情获取失败，请检查网络或稍后重试。")
        time.sleep(1)


def _ticker_worker() -> None:
    """行情 ticker 独立刷新循环。
    
    修复：同时刷新当前交易对象和所有开仓持仓的币种行情，
    确保持仓币种的 ticker 不会过期导致自动卖出失败。
    """
    from services.live_auto_pilot import load_live_auto_positions
    from services.sim_trade_engine import get_open_positions

    def priority_symbols() -> list[str]:
        positions: list[str] = []
        others: list[str] = []
        current_symbol = market_cache.get_current_symbol()
        if current_symbol:
            others.append(current_symbol)
        try:
            for position in get_open_positions():
                symbol = str(position.get("symbol") or "").upper().strip()
                if symbol:
                    positions.append(symbol)
        except Exception as exc:
            _debug_log(LIVE_CACHE_LOG, f"priority_sim_positions_failed error={repr(exc)}")
        try:
            for bot in load_grid_bots():
                if bot.get("status") == "running":
                    symbol = str(bot.get("symbol") or "").upper().strip()
                    if symbol:
                        positions.append(symbol)
        except Exception as exc:
            _debug_log(LIVE_CACHE_LOG, f"priority_grid_bots_failed error={repr(exc)}")
        try:
            for position in load_live_auto_positions(1000):
                if position.get("status") == "open":
                    symbol = str(position.get("symbol") or "").upper().strip()
                    if symbol:
                        positions.append(symbol)
        except Exception as exc:
            _debug_log(LIVE_CACHE_LOG, f"priority_live_auto_positions_failed error={repr(exc)}")
        rankings = market_cache.get_rankings() or {}
        try:
            for row in collect_top10_opportunities(rankings, limit=10):
                symbol = str(row.get("symbol") or "").upper().strip()
                if symbol:
                    others.append(symbol)
        except Exception as exc:
            _debug_log(LIVE_CACHE_LOG, f"priority_top10_failed error={repr(exc)}")
        try:
            status = get_fast_opportunity_status()
            candidate_rows = list(status.get("latest_multi_review", []) or [])
            latest_candidate = status.get("latest_candidate") or {}
            if latest_candidate:
                candidate_rows.append(latest_candidate)
            for row in candidate_rows:
                symbol = str(row.get("symbol") or "").upper().strip()
                if symbol:
                    others.append(symbol)
        except Exception as exc:
            _debug_log(LIVE_CACHE_LOG, f"priority_candidates_failed error={repr(exc)}")

        ordered: list[str] = []
        for group in (positions, others):
            for symbol in group:
                if symbol and symbol not in ordered:
                    ordered.append(symbol)
        return ordered[:MAX_PRIORITY_REFRESH_PER_LOOP]
    
    while True:
        started_at = time.monotonic()
        try:
            symbols = priority_symbols()
            success = 0
            failed = 0
            for symbol in symbols:
                try:
                    refresh_symbol_now(symbol)
                    success += 1
                except Exception as exc:
                    failed += 1
                    _debug_log(LIVE_CACHE_LOG, f"priority_refresh_failed symbol={symbol} error={repr(exc)}")
            _debug_log(LIVE_CACHE_LOG, f"priority_refresh symbols={len(symbols)} success={success} failed={failed}")
            
            if market_cache.consume_refresh_request():
                refresh_symbol_now(market_cache.get_current_symbol())
        except Exception as exc:
            print(f"[AI模型7.1.2] 行情后台刷新异常 error={repr(exc)}")
            market_cache.set_error("Binance行情获取失败，请检查网络或稍后重试。")
        time.sleep(max(0.5, 5.0 - (time.monotonic() - started_at)))


def _kline_worker() -> None:
    """K线独立刷新循环。"""
    last_refresh = 0.0
    while True:
        started_at = time.monotonic()
        try:
            now = time.monotonic()
            if now - last_refresh >= 5:
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


def _worker_specs() -> tuple[tuple[str, Callable[[], None]], ...]:
    return (
        ("market-cache-refresher", _worker),
        ("ticker-cache-refresher", _ticker_worker),
        ("kline-cache-refresher", _kline_worker),
        ("orderbook-cache-refresher", _orderbook_worker),
        ("derivatives-cache-refresher", _derivatives_worker),
        ("whale-cache-refresher", _whale_worker),
    )


def _ensure_worker_threads_locked() -> int:
    started = 0
    for name, target in _worker_specs():
        if _start_thread_locked(name, target):
            started += 1
    return started


def _watchdog_worker() -> None:
    """Restart background refresh threads if a worker exits unexpectedly."""
    while True:
        time.sleep(15)
        try:
            with _START_LOCK:
                restarted = _ensure_worker_threads_locked()
            if restarted:
                _debug_log(LIVE_CACHE_LOG, f"background_watchdog_restarted count={restarted}")
        except Exception as exc:
            _debug_log(LIVE_CACHE_LOG, f"background_watchdog_failed error={repr(exc)}")


def start_background_refresher() -> None:
    """启动后台刷新服务；已启动时会检查线程存活并补齐。"""
    global _STARTED
    with _START_LOCK:
        if not _acquire_process_lock_locked():
            return
        started = _ensure_worker_threads_locked()
        _start_thread_locked("background-refresher-watchdog", _watchdog_worker)
        if started or not _STARTED:
            _debug_log(LIVE_CACHE_LOG, f"background_refresher_ready started={started}")
        _STARTED = True


def run_background_refresher_forever() -> None:
    """Run the background refresher without requiring a Streamlit page session."""
    start_background_refresher()
    _debug_log(LIVE_CACHE_LOG, "background_refresher_forever_started")
    while True:
        time.sleep(3600)


if __name__ == "__main__":
    run_background_refresher_forever()
