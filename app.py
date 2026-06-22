"""AI模型 9.0 交易版。

本版本在 7.1.1 基础上优化盘口、交易对象联动和局部刷新体验。
仍然只使用 Binance 公共行情、公共K线和公共盘口数据，不接入 AI、账户 API、实盘交易或 WebSocket。
"""

from __future__ import annotations

import json
import os
import time
from html import escape
from pathlib import Path
from textwrap import dedent
from typing import Any

import streamlit as st

from services import market_cache
from services.ai_committee_engine import run_committee_meeting
from services.background_refresher import (
    refresh_klines_now,
    refresh_orderbook_now,
    refresh_symbol_now,
    refresh_whales_now,
    start_background_refresher,
)
from services.binance_public import get_all_24hr_tickers
from services.capital_structure_engine import analyze_capital_structure
from services.liquidation_engine import analyze_liquidation_risk
from services.local_strategy_engine import append_strategy_log, build_local_strategy
from services.market_risk_radar import analyze_market_risk_radar
from services.market_scanner import scan_market_opportunities
from services.local_api_server import start_local_api_server
from services.orderbook_analyzer import analyze_orderbook
from services.signal_engine import build_signal_analysis
from services.server_runtime import (
    apply_safe_startup,
    get_server_health,
    load_server_settings,
)
from services.user_account import (
    account_login_enabled,
    authenticate_user,
    create_admin_user,
    has_any_user,
    validate_session,
)
from services.fast_opportunity_engine import get_fast_opportunity_status, run_committee_top10_precheck
from services.sim_trade_engine import (
    cancel_order,
    calculate_position_holding_time,
    calculate_position_r_multiple,
    calculate_sim_score_feedback,
    clear_sim_history,
    close_sim_position,
    get_sim_equity_curve,
    load_sim_diagnostics,
    load_settings,
    move_stop_to_breakeven,
    reset_sim_account,
    save_settings,
    set_sim_status,
    update_simulation,
)
from services.sim_observability import build_sim_diagnostic_rows, build_sim_score_feedback_rows
from services.runtime_file_guard import ensure_runtime_files
from services.trading_database import (
    get_sim_trade_stats as get_persistent_sim_trade_stats,
    init_database,
    query_review_records,
    query_sim_trades,
)
from app_pages.dashboard_page import render_data_dashboard_page
from app_pages.home_page import render_home
from app_pages.learning_page import render_learning
from app_pages.simulation_page import render_trading
from app_pages.positions_page import render_positions
from app_pages.trade_records_page import render_trade_records_page
from app_pages.watchlist_page import render_watchlist as render_watchlist_page
from app_pages.kline_page import (
    KLINE_INTERVALS,
    MA_OPTIONS,
    build_ai_status,
    calculate_ma,
    local_scores,
    score_color,
    score_text,
)
from app_pages.market_page import render_market_page
from app_pages.strategy_factory_page import render_strategy_factory
from app_pages.server_page import render_server_health_page
from app_pages.profile_page import render_profile
from app_pages.remote_page import render_remote_control_page
from app_pages.live_page import render_live_trading_center
from app_pages.auto_trade_page import render_approval_center_page
from app_pages.signals_page import render_committee_overview_window, render_signals, _safe_committee_text
from app_state.session import (
    ensure_current_device,
    initialize_session_state,
    init_state,
    is_user_selected_symbol_source,
    on_symbol_change,
    refresh_all_now,
    set_current_symbol,
)
from components.market_widgets import render_opportunity_list, render_rank_list
from components.opportunity_board import _combined_trade_opportunities, render_trade_opportunity_board as render_trade_opportunity_board_widget
from components.styles import inject_styles
from components.symbol_selector import render_symbol_search_panel
from components.topbar import get_effective_ticker, render_fixed_market_bar
from components.ui import kline_symbol_link, render_kline_jump_links, render_metric_grid, render_page_head
from services.whale_monitor import analyze_dealer_behavior


APP_TITLE = "AI模型 10.0"
APP_SUBTITLE = "Binance AI Assistant Mobile First"
VERSION = "拆分功能区模拟交易11.0版"
FALLBACK_SYMBOLS = ["BTCUSDT", "ETHUSDT", "BNBUSDT", "SOLUSDT", "DOGEUSDT", "XRPUSDT"]
LOG_DIR = Path(__file__).resolve().parent / "logs"
POSITION_PRICE_LOG = LOG_DIR / "position_price_debug.log"
SIGNAL_CHAIN_LOG = LOG_DIR / "signal_chain_debug.log"


def append_debug_log(path: Path, message: str) -> None:
    """Write short diagnostics without secrets."""
    try:
        LOG_DIR.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as handle:
            handle.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')} {message}\n")
    except Exception:
        pass

NAV_ITEMS = [
    ("总览", "home", "⌂"),
    ("市场", "market", "▥"),
    ("信号", "signals", "◇"),
    ("交易", "trading", "⇄"),
    ("记录", "trade_records", "≡"),
    ("持仓", "positions", "▤"),
    ("学习", "learning", "▣"),
    ("策略", "strategy", "◎"),
    ("安全", "live", "◆"),
    ("自动", "auto_trade", "▶"),
    ("数据", "dashboard", "▦"),
    ("远控", "remote", "▣"),
    ("服务器", "server", "▧"),
    ("我的", "profile", "◉"),
]

PAGE_TITLES = {
    "home": ("总览", "快速查看当前交易对象、评分、行情概览和预留AI建议。"),
    "market": ("市场", "专业列表方式查看涨幅榜、跌幅榜和成交量榜。"),
    "signals": ("信号", "K线、均线、金叉死叉、本地策略和市场结构预留。"),
    "trading": ("交易", "模拟交易、人工交易和自动交易入口预留。"),
    "trade_records": ("交易记录", "查看持久化模拟交易记录、统计中心和自动复盘。"),
    "positions": ("持仓", "持仓、盈亏、成交记录和复盘入口预留。"),
    "learning": ("学习", "技术指标、市场结构和交易知识学习中心。"),
    "strategy": ("策略工厂", "创建策略、配置参数、历史回测、策略评级和候选策略验证。"),
    "live": ("实盘安全", "实盘交易前置安全中心、只读监控、Dry-run 和执行前检查。"),
    "auto_trade": ("自动交易", "小资金自动实盘、现货/永续合约、自动开关、仓位比例、杠杆和风控状态。"),
    "approval": ("自动交易", "兼容旧链接：审批中心已取消，改为自动交易控制台。"),
    "dashboard": ("数据看板", "平台数据总览、交易表现、委员表现、风控表现和日报/周报/月报。"),
    "remote": ("远程控制", "多设备访问、通知中心、远程暂停、紧急停止和操作审计。"),
    "server": ("服务器健康", "服务器部署、长期运行、日志、备份、远程访问和安全启动检查。"),
    "profile": ("我的", "系统状态、选择交易对象、数据源状态和版本信息。"),
}


def _temporary_initial_rankings(symbols: list[str] | None = None) -> dict[str, list[dict[str, Any]]]:
    """机会榜未完成扫描时的安全降级数据，明确标记为初始化中。"""
    base_symbols = [symbol for symbol in (symbols or FALLBACK_SYMBOLS) if str(symbol).endswith("USDT")]
    if not base_symbols:
        base_symbols = FALLBACK_SYMBOLS
    rows = []
    for symbol in base_symbols[:4]:
        rows.append(
            {
                "symbol": symbol,
                "last_price": 0,
                "price_change_percent": 0,
                "quote_volume": 0,
                "final_opportunity_score": 0,
                "raw_opportunity_score": 0,
                "risk_score": 0,
                "risk_penalty": 0,
                "score_cap": 0,
                "opportunity_status": "数据初始化中",
                "current_market_state": "初始化中",
                "direction": "等待数据",
                "advice": "等待真实扫描",
                "opportunity_source": "初始化降级榜",
                "is_temporary": True,
            }
        )
    return {
        "gainers": rows,
        "losers": rows,
        "volume": rows,
        "long_opportunities": rows,
        "short_opportunities": [],
        "strong": rows,
        "weak": [],
        "abnormal": [],
        "high_risk": [],
    }


def safe_fetch_initial_market_data(symbol: str) -> dict[str, Any]:
    """首次进入页面时安全获取当前交易对象行情，失败时保留旧缓存。"""
    normalized = str(symbol or "BTCUSDT").upper().strip()
    result = {
        "ok": False,
        "symbol": normalized,
        "price": None,
        "change_24h": None,
        "market_status": "初始化中",
        "error": "",
    }
    try:
        ticker = market_cache.get_ticker(normalized)
        if not ticker:
            refresh_symbol_now(normalized)
            ticker = market_cache.get_ticker(normalized)
        if ticker:
            price = ticker.get("last_price")
            change = ticker.get("price_change_percent")
            st.session_state["ticker_data"] = ticker
            st.session_state["current_price"] = price
            st.session_state["current_24h_change"] = change
            result.update({"ok": True, "price": price, "change_24h": change, "market_status": "在线"})
            return result
        cached = st.session_state.get("ticker_data") or {}
        if cached:
            result.update(
                {
                    "ok": True,
                    "price": cached.get("last_price"),
                    "change_24h": cached.get("price_change_percent"),
                    "market_status": "数据延迟",
                }
            )
            return result
        result.update({"market_status": "获取失败", "error": "当前行情暂未返回，系统将自动重试。"})
        return result
    except Exception as exc:
        message = f"初始行情获取失败：{repr(exc)}"
        st.session_state["last_error"] = message
        result.update({"market_status": "获取失败", "error": message})
        return result


def _bootstrap_rankings(symbols: list[str]) -> dict[str, list[dict[str, Any]]]:
    """首次进入页面时主动生成一次机会榜，失败时使用初始化降级榜。"""
    cached = market_cache.get_rankings()
    if cached:
        return cached
    try:
        valid_symbols = symbols if len(symbols) > len(FALLBACK_SYMBOLS) else []
        tickers = get_all_24hr_tickers(set(valid_symbols) if valid_symbols else None)
        rankings = {
            "gainers": sorted(tickers, key=lambda item: item["price_change_percent"], reverse=True)[:10],
            "losers": sorted(tickers, key=lambda item: item["price_change_percent"])[:10],
            "volume": sorted(tickers, key=lambda item: item["quote_volume"], reverse=True)[:10],
        }
        rankings.update(scan_market_opportunities(tickers))
        market_cache.set_rankings(rankings)
        return rankings
    except Exception as exc:
        st.session_state["last_error"] = f"机会榜初始化失败：{repr(exc)}"
        return _temporary_initial_rankings(symbols)


def bootstrap_initial_data() -> dict[str, Any]:
    """首次进入页面前预加载行情、机会榜、当前交易对象和委员会目标。"""
    initialize_session_state()
    status = dict(st.session_state.get("bootstrap_status") or {})
    if st.session_state.get("bootstrap_done"):
        return {"ok": bool(st.session_state.get("data_ready")), "status": status}

    symbol = str(st.session_state.get("current_symbol") or "BTCUSDT").upper().strip()
    market_result = safe_fetch_initial_market_data(symbol)
    status["market"] = market_result.get("market_status", "初始化中")
    if market_result.get("error"):
        status["error"] = market_result["error"]

    interval = market_cache.get_kline_interval()
    if len(market_cache.get_klines(symbol, interval)) < 60:
        try:
            refresh_klines_now(symbol, interval)
        except Exception:
            pass

    symbols = market_cache.get_symbols(FALLBACK_SYMBOLS)
    rankings = _bootstrap_rankings(symbols)
    board = _combined_trade_opportunities(rankings, 10)
    st.session_state["opportunity_board"] = board
    status["opportunity"] = "已加载" if board else "初始化中"

    target = symbol
    if st.session_state.get("committee_target_mode") == "best_opportunity" and board:
        first_real = next((row for row in board if not row.get("is_temporary")), board[0])
        target = str(first_real.get("symbol") or symbol).upper().strip()
        st.session_state["committee_anchor_source"] = "机会榜TOP1" if not first_real.get("is_temporary") else "初始化降级榜"
        st.session_state["committee_review_queue_symbol"] = target or symbol
        st.session_state.setdefault("committee_active_symbol", symbol)
        st.session_state.setdefault("committee_target_symbol", st.session_state.get("committee_active_symbol", symbol))
    status["committee"] = "已加载" if st.session_state["committee_target_symbol"] else "初始化中"
    st.session_state["market_snapshot"] = market_cache.snapshot()
    st.session_state["data_ready"] = bool(market_result.get("ok") or board)
    st.session_state["bootstrap_done"] = True
    st.session_state["bootstrap_status"] = status
    return {"ok": bool(st.session_state["data_ready"]), "status": status}


def refresh_page_data() -> None:
    """首次进入和导航切换共用的数据刷新入口。"""
    symbol = str(st.session_state.get("current_symbol") or "BTCUSDT").upper().strip()
    now = time.time()
    if not market_cache.get_ticker(symbol) and now - float(st.session_state.get("last_market_refresh", 0) or 0) > 3:
        safe_fetch_initial_market_data(symbol)
        st.session_state["last_market_refresh"] = now
    if not market_cache.get_rankings() and now - float(st.session_state.get("last_opportunity_refresh", 0) or 0) > 5:
        rankings = _bootstrap_rankings(market_cache.get_symbols(FALLBACK_SYMBOLS))
        st.session_state["opportunity_board"] = _combined_trade_opportunities(rankings, 10)
        st.session_state["last_opportunity_refresh"] = now


def render_bootstrap_status() -> None:
    """首次加载异常或较慢时，给出中文状态提示。"""
    status = dict(st.session_state.get("bootstrap_status") or {})
    if not status:
        return
    elapsed = time.time() - float(status.get("started_at", time.time()) or time.time())
    error = str(status.get("error") or st.session_state.get("last_error") or "")
    if st.session_state.get("data_ready") and not error and elapsed < 10:
        return
    slow_note = "初始化较慢，请检查网络或 Binance API 连接。" if elapsed >= 10 and not st.session_state.get("data_ready") else ""
    st.markdown(
        dedent(f"""
        <div class="app-shell">
          <div class="status-card">
            <b>系统正在初始化数据...</b><br>
            行情：{escape(str(status.get("market", "初始化中")))}｜
            机会榜：{escape(str(status.get("opportunity", "初始化中")))}｜
            委员会：{escape(str(status.get("committee", "初始化中")))}<br>
            {escape(error or slow_note)}
          </div>
        </div>
        """),
        unsafe_allow_html=True,
    )


def safe_number(value: Any, default: float | None = None) -> float | None:
    try:
        if value is None:
            return default
        if isinstance(value, str) and not value.strip():
            return default
        number = float(value)
        if number != number:
            return default
        return number
    except Exception:
        return default


def safe_score(value: Any, default: float | None = None) -> float | None:
    return safe_number(value, default)


def safe_compare_lt(value: Any, threshold: float) -> bool:
    number = safe_score(value)
    return False if number is None else number < threshold


def safe_compare_gte(value: Any, threshold: float) -> bool:
    number = safe_score(value)
    return False if number is None else number >= threshold


def get_risk_class(risk_score: Any) -> str:
    risk = safe_score(risk_score)
    if risk is None:
        return "yellow"
    if risk < 35:
        return "green"
    if risk < 65:
        return "yellow"
    return "red"


def get_opportunity_class(opportunity_score: Any) -> str:
    score = safe_score(opportunity_score)
    if score is None:
        return "yellow"
    if score >= 80:
        return "green"
    if score >= 60:
        return "yellow"
    return "blue"


def format_score(score: Any, loading: str = "计算中") -> str:
    number = safe_score(score)
    if number is None:
        return loading
    return str(int(number)) if float(number).is_integer() else f"{number:.1f}"


def format_price(value: Any) -> str:
    number = safe_number(value)
    if number is None:
        return "正在获取"
    if number >= 1000:
        return f"{number:,.2f}"
    if number >= 1:
        return f"{number:,.4f}"
    return f"{number:,.8f}".rstrip("0").rstrip(".")


def format_waiting_price(value: Any) -> str:
    number = safe_number(value)
    if number is None or number <= 0:
        return "等待价格刷新"
    return format_price(number)


def valid_price(value: Any) -> float | None:
    number = safe_number(value)
    return number if number is not None and number > 0 else None


def format_percent(value: Any) -> str:
    number = safe_number(value)
    if number is None:
        return "正在获取"
    sign = "+" if number > 0 else ""
    return f"{sign}{number:.2f}%"


def format_compact(value: Any) -> str:
    number = safe_number(value)
    if number is None:
        return "正在获取"
    if abs(number) >= 1_000_000_000:
        return f"{number / 1_000_000_000:.2f}B"
    if abs(number) >= 1_000_000:
        return f"{number / 1_000_000:.2f}M"
    if abs(number) >= 1_000:
        return f"{number / 1_000:.2f}K"
    return f"{number:.2f}"


def _fragment_1s(func):
    """启用 Streamlit 1秒局部刷新，失败时降级为普通渲染。"""
    fragment = getattr(st, "fragment", None) or getattr(st, "experimental_fragment", None)
    if fragment:
        try:
            return fragment(run_every="1s")(func)
        except Exception as exc:
            print(f"[AI模型7.1.2] 局部刷新启用失败 func={func.__name__} error={repr(exc)}")
    return func


def _top10_precheck_map(rankings: dict[str, list[dict[str, Any]]] | None) -> dict[str, dict[str, Any]]:
    try:
        results = run_committee_top10_precheck(rankings, 10)
    except Exception:
        results = list((get_fast_opportunity_status().get("latest_top10_precheck") or []))
    return {str(item.get("symbol", "")).upper(): item for item in results}


def _multi_review_map() -> dict[str, dict[str, Any]]:
    return {str(item.get("symbol", "")).upper(): item for item in (get_fast_opportunity_status().get("latest_multi_review") or [])}


def anchor_current_symbol_to_fast_top1(rankings: dict[str, list[dict[str, Any]]] | None = None) -> None:
    """Keep the global current symbol, topbar and committee target anchored to opportunity TOP1."""
    current_source = str(st.session_state.get("current_symbol_source", ""))
    if current_source == "manual_select" or current_source.endswith("_search") or current_source.startswith("watch_"):
        current = str(st.session_state.get("current_symbol", "")).upper().strip()
        st.session_state["committee_active_symbol"] = current
        st.session_state["committee_target_symbol"] = current
        st.session_state["committee_anchor_source"] = "用户手动查看"
        return
    status = get_fast_opportunity_status()
    settings = status.get("settings") or {}
    if not bool(settings.get("ENABLE_COMMITTEE_ANCHOR_TOP1", True)):
        return
    top_rows = _combined_trade_opportunities(rankings, 10) if rankings else []
    top1 = top_rows[0] if top_rows else {}
    target = str(top1.get("symbol") or status.get("current_target") or "").upper().strip()
    if not target:
        return
    selected_target = target
    selected_source = "opportunity_top1_default"
    if selected_target != str(st.session_state.get("current_symbol", "")).upper() and current_source in {"", "default_bootstrap", "url_param", "opportunity_top1_default", "candidate_auto_switch", "opportunity_board_click"}:
        set_current_symbol(selected_target, source=selected_source)
    st.session_state["committee_active_symbol"] = selected_target
    st.session_state["committee_target_symbol"] = selected_target
    st.session_state["committee_review_queue_symbol"] = target
    st.session_state["committee_anchor_source"] = "机会榜TOP1默认对象"
    try:
        if not market_cache.get_ticker(selected_target):
            refresh_symbol_now(selected_target)
        if len(market_cache.get_klines(selected_target, market_cache.get_kline_interval())) < 60:
            refresh_klines_now(selected_target, market_cache.get_kline_interval())
    except Exception:
        pass


def build_current_local_strategy(symbol: str, ticker: dict[str, Any] | None) -> dict[str, Any]:
    """基于当前缓存生成本地策略委员结果，不触发外部AI。"""
    live_symbol = str(symbol or st.session_state.get("current_symbol", "BTCUSDT")).upper().strip()
    interval = market_cache.get_kline_interval()
    rows = market_cache.get_klines(live_symbol, interval)
    live_ticker = get_effective_ticker(live_symbol) or ticker
    current_price = live_ticker.get("last_price") if live_ticker else None
    orderbook_analysis = analyze_orderbook(market_cache.get_orderbook(live_symbol), current_price)
    analysis = build_signal_analysis(live_ticker, rows, orderbook_analysis)
    derivatives = market_cache.get_derivatives(live_symbol)
    capital = analyze_capital_structure(derivatives, live_ticker, analysis)
    liquidation = analyze_liquidation_risk(live_ticker, rows, derivatives, orderbook_analysis, analysis)
    whale = market_cache.get_whales(live_symbol)
    dealer = analyze_dealer_behavior(whale, derivatives, orderbook_analysis, analysis, liquidation)
    radar = analyze_market_risk_radar(live_ticker, rows, derivatives, capital, liquidation, whale, dealer, orderbook_analysis, analysis)
    return build_local_strategy(
        symbol=live_symbol,
        ticker=live_ticker,
        rows=rows,
        signal_analysis=analysis,
        orderbook_analysis=orderbook_analysis,
        derivatives=derivatives,
        capital=capital,
        liquidation=liquidation,
        whale=whale,
        dealer=dealer,
        radar=radar,
        primary_timeframe=interval,
    )


def build_current_committee_decision(symbol: str, ticker: dict[str, Any] | None) -> dict[str, Any]:
    """为总览页生成与信号页同源的委员会精简决议。"""
    live_symbol = str(symbol or st.session_state.get("current_symbol", "BTCUSDT")).upper().strip()
    interval = market_cache.get_kline_interval()
    rows = market_cache.get_klines(live_symbol, interval)
    passed_symbol = str((ticker or {}).get("symbol") or "").upper().strip()
    live_ticker = get_effective_ticker(live_symbol) or (ticker if passed_symbol == live_symbol else None)
    current_price = live_ticker.get("last_price") if live_ticker else None
    orderbook_analysis = analyze_orderbook(market_cache.get_orderbook(live_symbol), current_price)
    analysis = build_signal_analysis(live_ticker, rows, orderbook_analysis)
    derivatives = market_cache.get_derivatives(live_symbol)
    capital = analyze_capital_structure(derivatives, live_ticker, analysis)
    liquidation = analyze_liquidation_risk(live_ticker, rows, derivatives, orderbook_analysis, analysis)
    whale = market_cache.get_whales(live_symbol)
    dealer = analyze_dealer_behavior(whale, derivatives, orderbook_analysis, analysis, liquidation)
    radar = analyze_market_risk_radar(live_ticker, rows, derivatives, capital, liquidation, whale, dealer, orderbook_analysis, analysis)
    strategy = build_local_strategy(
        symbol=live_symbol,
        ticker=live_ticker,
        rows=rows,
        signal_analysis=analysis,
        orderbook_analysis=orderbook_analysis,
        derivatives=derivatives,
        capital=capital,
        liquidation=liquidation,
        whale=whale,
        dealer=dealer,
        radar=radar,
        primary_timeframe=interval,
    )
    return run_committee_meeting(
        live_symbol,
        ticker=live_ticker,
        rows=rows,
        signal_analysis=analysis,
        orderbook_analysis=orderbook_analysis,
        derivatives=derivatives,
        capital=capital,
        liquidation=liquidation,
        whale=whale,
        dealer=dealer,
        radar=radar,
        local_strategy=strategy,
    )


def render_trade_opportunity_board(rankings: dict[str, list[dict[str, Any]]] | None, compact: bool = False) -> None:
    render_trade_opportunity_board_widget(rankings, compact=compact, set_current_symbol=set_current_symbol)


render_trade_opportunity_board_realtime = render_trade_opportunity_board




def render_watchlist(rankings: dict[str, list[dict[str, Any]]]) -> None:
    render_watchlist_page(rankings, set_current_symbol=set_current_symbol)



def render_error(snapshot: dict[str, Any]) -> None:
    """仅在有错误时显示，不占用正常页面空间。"""
    if snapshot.get("last_error"):
        st.markdown(f'<div class="app-shell"><div class="error-box">{snapshot["last_error"]}</div></div>', unsafe_allow_html=True)


def render_page(page_key: str, symbol: str, ticker: dict[str, Any] | None, rankings: dict[str, list[dict[str, Any]]] | None, snapshot: dict[str, Any], symbols: list[str], scores: dict[str, Any]) -> None:
    """根据当前页面渲染独立内容。"""
    if page_key == "home":
        render_home(
            ticker,
            snapshot,
            scores,
            symbols,
            rankings,
            page_titles=PAGE_TITLES,
            version=VERSION,
            fallback_symbols=FALLBACK_SYMBOLS,
            set_current_symbol=set_current_symbol,
            render_trade_opportunity_board=render_trade_opportunity_board,
            render_committee_overview=render_committee_overview_window,
            build_current_committee_decision=build_current_committee_decision,
            build_current_local_strategy=build_current_local_strategy,
        )
    elif page_key == "market":
        render_market_page(rankings, PAGE_TITLES, VERSION, render_trade_opportunity_board, render_rank_list, render_watchlist, render_opportunity_list)
    elif page_key == "signals":
        render_signals(symbol, ticker, scores, PAGE_TITLES, VERSION, lambda message: append_debug_log(SIGNAL_CHAIN_LOG, message))
    elif page_key == "trading":
        render_trading(build_current_committee_decision, PAGE_TITLES, VERSION)
    elif page_key == "trade_records":
        render_trade_records_page(PAGE_TITLES, VERSION)
    elif page_key == "positions":
        render_positions(PAGE_TITLES, VERSION, str(st.session_state.get("current_symbol", "BTCUSDT")))
    elif page_key == "learning":
        render_learning(PAGE_TITLES, VERSION)
    elif page_key == "strategy":
        render_strategy_factory(PAGE_TITLES, VERSION, FALLBACK_SYMBOLS)
    elif page_key == "live":
        render_live_trading_center(PAGE_TITLES, VERSION, build_current_committee_decision, _safe_committee_text)
    elif page_key in {"approval", "auto_trade"}:
        render_approval_center_page(PAGE_TITLES, VERSION, build_current_committee_decision, _safe_committee_text)
    elif page_key == "dashboard":
        render_data_dashboard_page(PAGE_TITLES, VERSION)
    elif page_key == "remote":
        render_remote_control_page(PAGE_TITLES, VERSION, ensure_current_device)
    elif page_key == "server":
        render_server_health_page(PAGE_TITLES, VERSION)
    elif page_key == "profile":
        render_profile(
            symbols,
            snapshot,
            page_titles=PAGE_TITLES,
            version=VERSION,
            fallback_symbols=FALLBACK_SYMBOLS,
            set_symbol=set_current_symbol,
            refresh_callback=refresh_all_now,
        )


def render_bottom_nav() -> None:
    """渲染全局底部导航。"""
    active = st.session_state.active_page
    symbol = str(st.session_state.get("current_symbol") or "BTCUSDT").upper().strip()
    nav_html = ['<div class="bottom-nav"><div class="bottom-nav-inner">']
    for label, key, icon in NAV_ITEMS:
        klass = "nav-item active" if key == active else "nav-item"
        nav_html.append(f'<a class="{klass}" href="?page={key}&symbol={symbol}" target="_self"><div class="nav-icon">{icon}</div><div>{label}</div></a>')
    nav_html.append("</div></div>")
    st.markdown("".join(nav_html), unsafe_allow_html=True)


def enforce_simple_auth() -> bool:
    """简单访问密码预留：默认关闭，公网部署时可通过 .env 开启。"""
    settings = load_server_settings()
    enabled = bool(settings.get("enable_simple_auth")) or str(os.environ.get("ENABLE_SIMPLE_AUTH", "false")).lower() in {"1", "true", "yes", "on"}
    password = os.environ.get("APP_ACCESS_PASSWORD", "")
    if not enabled:
        return True
    if not password:
        st.warning("简单认证已开启，但 APP_ACCESS_PASSWORD 未设置。为安全起见，仅显示登录页。")
        return False
    if st.session_state.get("simple_auth_ok"):
        return True
    inject_styles()
    st.markdown(
        """
        <div class="app-shell">
          <div class="module-card warning-box">
            <b>访问保护</b><br>
            当前服务器已开启简单访问密码。密码不会显示，也不会写入日志。
          </div>
        </div>
        """,
        unsafe_allow_html=True,
    )
    entered = st.text_input("访问密码", type="password")
    if st.button("进入系统", width="stretch"):
        if entered == password:
            st.session_state["simple_auth_ok"] = True
            st.rerun()
        else:
            st.error("访问密码错误。")
    return False


def enforce_account_login() -> bool:
    """账户登录增强：默认关闭，开启后要求管理员账户和有效会话。"""
    if not account_login_enabled():
        return True
    device = st.session_state.get("current_device") or {}
    device_id = str(device.get("device_id", ""))
    inject_styles()
    if not has_any_user():
        st.markdown('<div class="app-shell"><div class="module-card warning-box"><b>首次创建管理员账户</b><br>账户系统已开启，请先创建 admin 管理员。密码不会明文保存。</div></div>', unsafe_allow_html=True)
        with st.form("first_admin_form"):
            username = st.text_input("管理员用户名", value="admin")
            display = st.text_input("显示名称", value="管理员")
            password = st.text_input("管理员密码", type="password")
            confirm = st.text_input("确认密码", type="password")
            if st.form_submit_button("创建管理员", width="stretch"):
                if password != confirm:
                    st.error("两次密码不一致。")
                else:
                    result = create_admin_user(username, password, display)
                    (st.success if result.get("ok") else st.error)(result.get("message"))
                    if result.get("ok"):
                        st.rerun()
        return False
    session_id = st.session_state.get("account_session_id")
    if session_id:
        session_result = validate_session(str(session_id))
        if session_result.get("ok"):
            st.session_state["current_user"] = session_result.get("user")
            st.session_state["current_account_session"] = session_result.get("session")
            return True
        st.session_state.pop("account_session_id", None)
        st.session_state.pop("current_user", None)
    st.markdown('<div class="app-shell"><div class="module-card warning-box"><b>账户登录</b><br>账户登录已开启。登录后才能进入系统，密码不会写入日志。</div></div>', unsafe_allow_html=True)
    with st.form("account_login_form"):
        username = st.text_input("用户名", value="admin")
        password = st.text_input("密码", type="password")
        if st.form_submit_button("登录", width="stretch"):
            result = authenticate_user(username, password, device_id)
            (st.success if result.get("ok") else st.error)(result.get("message"))
            if result.get("ok"):
                st.session_state["account_session_id"] = (result.get("session") or {}).get("session_id")
                st.session_state["current_user"] = result.get("user")
                st.rerun()
    return False


def main() -> None:
    """应用入口。"""
    st.set_page_config(page_title=f"{APP_TITLE} - {VERSION}", page_icon="📱", layout="wide", initial_sidebar_state="collapsed")
    if not st.session_state.get("runtime_file_guard_done"):
        st.session_state["runtime_file_guard_events"] = ensure_runtime_files()
        st.session_state["runtime_file_guard_done"] = True
    if not st.session_state.get("server_safe_startup_done"):
        st.session_state["server_safe_startup_result"] = apply_safe_startup()
        st.session_state["server_safe_startup_done"] = True
    if not enforce_simple_auth():
        return
    initialize_session_state()
    init_state(PAGE_TITLES, MA_OPTIONS)
    ensure_current_device()
    if not enforce_account_login():
        return
    bootstrap_result = bootstrap_initial_data()
    refresh_page_data()
    symbols = market_cache.get_symbols(FALLBACK_SYMBOLS)
    has_full_symbol_list = len(symbols) > len(FALLBACK_SYMBOLS)
    if has_full_symbol_list and st.session_state.current_symbol not in symbols and not is_user_selected_symbol_source():
        set_current_symbol(symbols[0])
    rankings = market_cache.get_rankings()
    anchor_current_symbol_to_fast_top1(rankings)
    snapshot = market_cache.snapshot()
    st.session_state["market_snapshot"] = snapshot
    current_symbol = st.session_state.current_symbol
    interval = market_cache.get_kline_interval()
    ticker = market_cache.get_ticker(current_symbol)
    kline_rows = market_cache.get_klines(current_symbol, interval)
    scores = local_scores(ticker, kline_rows)
    try:
        start_local_api_server()
    except Exception as exc:
        st.session_state["last_error"] = f"前端行情API启动失败，页面已降级运行：{exc!r}"
    start_background_refresher()
    inject_styles()
    render_fixed_market_bar(current_symbol, SIGNAL_CHAIN_LOG)
    render_error(snapshot)
    render_bootstrap_status()
    if bootstrap_result.get("ok") and not st.session_state.get("initial_load_done"):
        st.session_state["initial_load_done"] = True
        st.rerun()
    render_page(st.session_state.active_page, current_symbol, ticker, rankings, snapshot, symbols, scores)
    render_bottom_nav()


if __name__ == "__main__":
    main()
