"""AI模型 9.0 交易版。

本版本在 7.1.1 基础上优化盘口、交易对象联动和局部刷新体验。
仍然只使用 Binance 公共行情、公共K线和公共盘口数据，不接入 AI、账户 API、实盘交易或 WebSocket。
"""

from __future__ import annotations

import json
import os
import re
import time
from html import escape
from pathlib import Path
from textwrap import dedent
from typing import Any

import plotly.graph_objects as go
import streamlit as st
import streamlit.components.v1 as components
from plotly.offline import get_plotlyjs
from plotly.subplots import make_subplots

from services import market_cache
from services.ai_committee_engine import get_committee_candidates, run_committee_meeting
from services.approval_center import (
    approve_approval,
    create_cancel_order_approval,
    execute_approved_approval,
    get_approval_detail,
    get_approval_review_summary,
    get_approval_stats,
    get_pending_approvals,
    load_approval_audit_log,
    load_approval_queue,
    modify_approval,
    reject_approval,
    run_approval_preflight,
)
from services.background_refresher import (
    refresh_klines_now,
    refresh_orderbook_now,
    refresh_symbol_now,
    refresh_whales_now,
    start_background_refresher,
)
from services.binance_public import get_all_24hr_tickers, get_24hr_ticker
from services.capital_structure_engine import analyze_capital_structure
from services.liquidation_engine import analyze_liquidation_risk
from services.local_strategy_engine import append_strategy_log, build_local_strategy
from services.live_trading_center import (
    cancel_live_order,
    create_live_exit_plan,
    create_live_order_preview,
    create_live_order_plan,
    fetch_live_order_status,
    generate_live_position_review_snapshot,
    get_live_account_snapshot,
    get_live_manual_execution_status,
    get_live_position_summary,
    get_live_safety_status,
    load_live_position_audit_log,
    load_live_settings,
    load_live_order_records,
    preview_live_exit_order,
    release_live_kill_switch,
    run_exit_spot_test_order,
    run_futures_test_order,
    run_live_exit_preflight,
    run_live_futures_preflight,
    run_live_preflight_check,
    run_live_manual_preflight,
    run_spot_test_order,
    run_test_order_validation,
    run_testnet_order_flow,
    save_live_settings,
    submit_live_exit_order,
    submit_live_futures_order,
    submit_live_spot_order,
    trigger_live_kill_switch,
    validate_live_order_plan,
)
from services.live_auto_pilot import (
    create_live_auto_order_plan,
    disable_live_auto_pilot,
    enable_live_auto_pilot,
    execute_live_auto_spot_order,
    filter_live_auto_signal,
    get_live_auto_status,
    load_live_auto_audit_log,
    load_live_auto_config,
    pause_live_auto_pilot,
    release_live_auto_circuit_breaker,
    resume_live_auto_pilot,
    run_live_auto_admission_check,
    run_live_auto_exit_check,
    save_live_auto_config,
    trigger_live_auto_circuit_breaker,
)
from services.external_ai_center import (
    get_external_ai_performance_summary,
    get_external_ai_secret_status,
    get_external_ai_status,
    load_external_ai_audit_log,
    load_external_ai_settings,
    save_external_ai_settings,
    test_external_ai_connection,
    test_external_ai_ssl_environment,
)
from services.manual_position_override import (
    evaluate_manual_position_override,
    load_manual_position_override_log,
    save_manual_position_override,
)
from services.market_cognition_engine import build_market_cognition
from services.market_cognition_recorder import save_market_cognition_snapshot
from services.cognition_snapshot_validator import (
    build_snapshot_validation_report,
    save_snapshot_validation_report,
)
from services.experience_library_loader import (
    DEFAULT_EXPERIENCE_LIBRARY_VERSION,
    EXPERIENCE_LIBRARY_LABELS,
    EXPERIENCE_LIBRARY_VERSIONS,
    get_experience_library_data_sources,
    get_experience_library_versions_status,
    load_experience_library_summary,
)
from services.experience_fusion_engine import build_fused_experience_result
from services.experience_matcher import build_experience_query_from_cognition, match_experience
from services.market_risk_radar import analyze_market_risk_radar
from services.market_scanner import scan_market_opportunities
from services.local_api_server import get_local_api_port, start_local_api_server
from services.orderbook_analyzer import analyze_orderbook
from services.backtest_engine import (
    compare_strategy_results,
    export_strategy_report,
    load_backtest_results,
    run_backtest,
    run_batch_backtest,
    run_parameter_grid_search,
)
from services.replay_learning_engine import analyze_replay_learning
from services.signal_engine import build_signal_analysis
from services.secure_api_vault import clear_secure_api_values, get_secure_api_status, write_secure_api_values
from services.server_runtime import (
    apply_safe_startup,
    check_server_config,
    create_backup,
    get_backup_status,
    get_git_status,
    get_server_health,
    load_backup_settings,
    load_server_settings,
    rotate_logs,
    save_backup_settings,
    save_server_settings,
)
from services.notification_center import (
    archive_notification,
    create_notification,
    get_notification_rules,
    get_notification_summary,
    load_notifications,
    mark_all_notifications_read,
    mark_notification_read,
    save_notification_rules,
    trigger_approval_alert,
    trigger_risk_alert,
    trigger_server_alert,
)
from services.remote_control_center import (
    check_permission,
    get_current_device_info,
    get_remote_control_status,
    load_registered_devices,
    record_remote_action,
    register_device,
    require_confirmation,
    update_device_last_seen,
)
from services.user_account import (
    account_login_enabled,
    authenticate_user,
    bind_device_to_user,
    change_password,
    create_admin_user,
    expire_session,
    get_account_audit,
    get_account_status,
    get_login_audit,
    get_user_permissions,
    get_user_profile,
    has_any_user,
    save_user_profile,
    validate_session,
)
from services.cloud_sync_adapter import (
    get_cloud_sync_status,
    list_remote_backups,
    load_sync_audit,
    pull_data,
    save_sync_status,
    sync_approvals,
    sync_backups,
    sync_config_to_cloud,
    sync_notifications,
    sync_reports,
    upload_backup,
)
from services.dashboard_center import (
    collect_all_metrics,
    export_report_csv,
    export_report_json,
    export_report_markdown,
    generate_daily_report,
    generate_monthly_report,
    generate_weekly_report,
    load_recent_reports,
)
from services.fast_opportunity_engine import get_fast_opportunity_status, run_committee_top10_precheck, save_fast_opportunity_settings
from services.sim_trade_engine import (
    cancel_order,
    calculate_position_holding_time,
    calculate_position_r_multiple,
    clear_sim_history,
    close_sim_position,
    create_pending_sim_order,
    get_sim_account_summary,
    get_sim_equity_curve,
    load_settings,
    move_stop_to_breakeven,
    reset_sim_account,
    save_settings,
    set_sim_status,
    update_simulation,
    validate_signal_for_simulation,
)
from services.sim_trade_review_engine import (
    build_feedback_summary,
    load_feedback_summary,
    load_sim_trade_reviews,
)
from services.system_operations import (
    get_system_operations_status,
    load_recent_log_lines,
    run_aimodel_control,
)
from services.runtime_file_guard import ensure_runtime_files
from services.system_diagnostics import (
    load_last_diagnostics,
    load_recent_binance_logs,
    run_binance_diagnostics,
)
from services.trading_database import (
    get_sim_trade_stats as get_persistent_sim_trade_stats,
    init_database,
    query_review_records,
    query_sim_trades,
)
from services.strategy_factory import (
    create_strategy_candidate,
    get_available_strategies,
    get_replay_optimization_hints,
    get_strategy_candidates,
    get_strategy_config,
    reset_strategy_config,
    save_strategy_config,
)
from services.watchlist_manager import (
    add_to_watchlist,
    auto_add_from_rankings,
    get_watchlist,
    get_watchlist_alerts,
    get_watchlist_candidates_for_committee,
    get_watchlist_summary,
    is_watched,
    remove_from_watchlist,
    clear_expired_watchlist,
    set_watchlist_category,
    update_watchlist_item,
)
from services.whale_monitor import analyze_dealer_behavior


APP_TITLE = "AI模型 9.2.11"
APP_SUBTITLE = "Binance AI Assistant Mobile First"
VERSION = "AI模型 9.2.11 多经验库融合决策版"
FALLBACK_SYMBOLS = ["BTCUSDT", "ETHUSDT", "BNBUSDT", "SOLUSDT", "DOGEUSDT", "XRPUSDT"]
KLINE_INTERVALS = ["1m", "5m", "15m", "30m", "1h", "4h"]
MA_OPTIONS = ["MA5", "MA10", "MA20", "MA60", "MA120"]
LOG_DIR = Path(__file__).resolve().parent / "logs"
POSITION_PRICE_LOG = LOG_DIR / "position_price_debug.log"
SIGNAL_CHAIN_LOG = LOG_DIR / "signal_chain_debug.log"
EXPERIENCE_MATCH_LOG = LOG_DIR / "experience_match_debug.log"


def get_selected_experience_library_version() -> str:
    selected = str(st.session_state.get("experience_library_version") or DEFAULT_EXPERIENCE_LIBRARY_VERSION)
    return selected if selected in EXPERIENCE_LIBRARY_VERSIONS else DEFAULT_EXPERIENCE_LIBRARY_VERSION


def get_selected_experience_mode() -> str:
    selected = str(st.session_state.get("experience_mode") or "single")
    return "fused" if selected == "fused" else "single"


@st.cache_data(ttl=20, show_spinner=False)
def get_cached_experience_library_versions_status() -> dict[str, dict[str, Any]]:
    return get_experience_library_versions_status()


@st.cache_data(ttl=20, show_spinner=False)
def load_cached_experience_library_summary(version: str) -> dict[str, Any]:
    return load_experience_library_summary(version=version)


@st.cache_data(ttl=20, show_spinner=False)
def match_cached_experience(query: dict[str, Any], version: str, top_k: int = 50) -> dict[str, Any]:
    return match_experience(query, experience_version=version, top_k=top_k)


@st.cache_data(ttl=20, show_spinner=False)
def build_cached_fused_experience_result(query: dict[str, Any], top_k: int = 50) -> dict[str, Any]:
    return build_fused_experience_result(query, top_k=top_k)


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


def initialize_session_state() -> None:
    """统一初始化首屏需要的状态，不覆盖用户已有选择。"""
    defaults: dict[str, Any] = {
        "active_page": "home",
        "current_symbol": "BTCUSDT",
        "selected_symbol": "BTCUSDT",
        "current_symbol_source": "default_bootstrap",
        "current_symbol_updated_at": 0.0,
        "committee_target_mode": "best_opportunity",
        "committee_target_symbol": "BTCUSDT",
        "committee_active_symbol": "BTCUSDT",
        "committee_review_queue_symbol": "BTCUSDT",
        "committee_anchor_source": "默认交易对象",
        "experience_mode": "single",
        "experience_library_version": DEFAULT_EXPERIENCE_LIBRARY_VERSION,
        "selected_opportunity_symbol": "",
        "topbar_symbol": "BTCUSDT",
        "kline_symbol": "BTCUSDT",
        "orderbook_symbol": "BTCUSDT",
        "signal_symbol": "BTCUSDT",
        "market_snapshot": {},
        "ticker_data": {},
        "current_price": None,
        "current_24h_change": None,
        "opportunity_board": [],
        "watchlist_data": [],
        "committee_decision": {},
        "data_ready": False,
        "bootstrap_done": False,
        "initial_load_done": False,
        "bootstrap_status": {
            "market": "初始化中",
            "opportunity": "初始化中",
            "committee": "初始化中",
            "error": "",
            "started_at": time.time(),
        },
        "last_market_refresh": 0.0,
        "last_opportunity_refresh": 0.0,
        "last_committee_refresh": 0.0,
        "last_error": "",
    }
    for key, value in defaults.items():
        st.session_state.setdefault(key, value)


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


def init_state() -> None:
    """初始化页面状态，不直接请求 Binance。"""
    initialize_session_state()
    page = st.query_params.get("page", "home")
    st.session_state.active_page = page if page in PAGE_TITLES else "home"
    query_symbol = str(st.query_params.get("symbol", "") or "").upper().strip()
    watch_add_symbol = str(st.query_params.get("watch_add", "") or "").upper().strip()
    initial_symbol = query_symbol or market_cache.get_current_symbol()
    st.session_state.setdefault("current_symbol", initial_symbol)
    st.session_state.setdefault("selected_symbol", st.session_state.current_symbol)
    st.session_state.setdefault("symbol_search", "")
    st.session_state.setdefault("kline_interval", market_cache.get_kline_interval())
    st.session_state.setdefault("ma_visibility", MA_OPTIONS)
    st.session_state.setdefault("follow_latest", True)
    st.session_state.setdefault("chart_interactive", False)
    st.session_state.setdefault("watchlist", [])
    if query_symbol and query_symbol != st.session_state.current_symbol:
        set_current_symbol(query_symbol, source="url_param")
    if watch_add_symbol:
        add_to_watchlist(watch_add_symbol, source="市场榜单", category="ai")
    market_cache.set_current_symbol(st.session_state.current_symbol)
    market_cache.set_kline_interval(st.session_state.kline_interval)


def ensure_current_device() -> dict[str, Any]:
    """登记当前访问设备。Streamlit 先做 session 级设备识别。"""
    st.session_state.setdefault("device_id", f"dev_{id(st.session_state):x}")
    st.session_state.setdefault("device_name", "当前设备")
    page = st.session_state.get("active_page", "home")
    info = get_current_device_info(str(st.session_state.get("device_id")), "", page, str(st.session_state.get("device_name", "当前设备")))
    device = register_device(info)
    update_device_last_seen(str(device.get("device_id", "")), page)
    st.session_state["current_device"] = device
    return device


def on_symbol_change() -> None:
    """统一交易对象切换入口。"""
    set_current_symbol(st.session_state.selected_symbol, source="manual_select")


def is_user_selected_symbol_source(source: Any | None = None) -> bool:
    """判断当前交易对象是否来自用户点击、URL或搜索，避免被机会榜自动覆盖。"""
    value = str(source if source is not None else st.session_state.get("current_symbol_source", "")).strip()
    if not value:
        return False
    return (
        value in {"manual_select", "url_param", "opportunity_board_click"}
        or value.endswith("_search")
        or value.startswith("watch_")
    )


def set_current_symbol(symbol: str, source: str = "manual_select") -> None:
    """写入全局唯一当前交易对象，并同步顶部、K线、盘口、信号和委员会对象。"""
    normalized = str(symbol or "").upper().strip()
    if not normalized:
        return
    st.session_state.current_symbol = normalized
    st.session_state.selected_symbol = normalized
    st.session_state["current_symbol_source"] = source
    st.session_state["current_symbol_updated_at"] = time.time()
    st.session_state["topbar_symbol"] = normalized
    st.session_state["kline_symbol"] = normalized
    st.session_state["orderbook_symbol"] = normalized
    st.session_state["signal_symbol"] = normalized
    st.session_state["committee_active_symbol"] = normalized
    st.session_state["committee_target_symbol"] = normalized
    st.session_state["selected_opportunity_symbol"] = normalized if source == "opportunity_board_click" else st.session_state.get("selected_opportunity_symbol", "")
    market_cache.set_current_symbol(normalized)
    market_cache.request_refresh()
    refresh_errors: list[str] = []
    try:
        if not market_cache.get_ticker(normalized):
            refresh_symbol_now(normalized)
    except Exception as exc:
        refresh_errors.append(f"Ticker：{exc!r}")
    try:
        if len(market_cache.get_klines(normalized, market_cache.get_kline_interval())) < 60:
            refresh_klines_now(normalized, market_cache.get_kline_interval())
    except Exception as exc:
        refresh_errors.append(f"K线：{exc!r}")
    try:
        if not market_cache.get_orderbook(normalized):
            refresh_orderbook_now(normalized)
    except Exception as exc:
        refresh_errors.append(f"盘口：{exc!r}")
    try:
        if not market_cache.get_whales(normalized):
            refresh_whales_now(normalized)
    except Exception as exc:
        refresh_errors.append(f"大单：{exc!r}")
    if refresh_errors:
        st.session_state["last_error"] = "切换交易对象后部分数据刷新失败：" + "；".join(refresh_errors[:4])
    try:
        st.query_params["page"] = st.session_state.get("active_page", "home")
        st.query_params["symbol"] = normalized
    except Exception as exc:
        print(f"[AI模型7.0.9] 更新URL交易对象失败 error={repr(exc)}")


def anchor_current_symbol_to_fast_top1(rankings: dict[str, list[dict[str, Any]]] | None = None) -> None:
    """默认锚定机会榜TOP1；TOP1不满足时切换到已进入候选的币。"""
    if is_user_selected_symbol_source():
        current = str(st.session_state.get("current_symbol", "")).upper().strip()
        st.session_state["committee_active_symbol"] = current
        st.session_state["committee_target_symbol"] = current
        st.session_state["committee_anchor_source"] = "用户手动查看"
        return
    status = get_fast_opportunity_status()
    settings = status.get("settings") or {}
    if not bool(settings.get("ENABLE_COMMITTEE_ANCHOR_TOP1", True)):
        return
    trigger_score = int(settings.get("OPPORTUNITY_TRIGGER_SCORE", 80) or 80)
    top_rows = _combined_trade_opportunities(rankings, 10) if rankings else []
    top1 = top_rows[0] if top_rows else {}
    target = str(top1.get("symbol") or status.get("current_target") or "").upper().strip()
    score = safe_score(top1.get("final_opportunity_score", top1.get("opportunity_score")), safe_score(status.get("target_score"), 0)) or 0
    if not target:
        return
    precheck = _top10_precheck_map(rankings).get(target, {}) if rankings else {}
    multi = _multi_review_map()
    top1_multi = multi.get(target, {})
    risk = safe_score(top1.get("risk_score"), 100)
    top1_satisfies = bool(top1_multi.get("candidate_created")) or (bool(precheck.get("allowed_candidate")) and score >= trigger_score and risk is not None and risk < 70)
    candidate = next(
        (item for item in sorted(multi.values(), key=lambda row: int(row.get("rank", 999) or 999)) if item.get("candidate_created") and str(item.get("symbol", "")).upper()),
        None,
    )
    selected_target = target
    selected_source = "opportunity_top1_default"
    if not top1_satisfies and candidate:
        selected_target = str(candidate.get("symbol", target)).upper().strip()
        selected_source = "candidate_auto_switch"
    current_source = str(st.session_state.get("current_symbol_source", ""))
    if selected_target != str(st.session_state.get("current_symbol", "")).upper() and current_source in {"", "default_bootstrap", "opportunity_top1_default", "candidate_auto_switch"}:
        set_current_symbol(selected_target, source=selected_source)
    st.session_state["committee_active_symbol"] = selected_target
    st.session_state["committee_target_symbol"] = selected_target
    st.session_state["committee_review_queue_symbol"] = target
    st.session_state["committee_anchor_source"] = "候选币自动切换" if selected_source == "candidate_auto_switch" else "机会榜TOP1默认对象"
    try:
        if not market_cache.get_ticker(selected_target):
            refresh_symbol_now(selected_target)
        if len(market_cache.get_klines(selected_target, market_cache.get_kline_interval())) < 60:
            refresh_klines_now(selected_target, market_cache.get_kline_interval())
    except Exception:
        pass


def refresh_all_now() -> None:
    """手动刷新当前交易对象行情。"""
    market_cache.set_current_symbol(st.session_state.current_symbol)
    market_cache.request_refresh()


def on_kline_interval_change() -> None:
    """K线周期切换回调。"""
    market_cache.set_kline_interval(st.session_state.kline_interval)
    market_cache.request_kline_refresh()


def reset_follow_latest() -> None:
    """恢复K线跟随最新价格。"""
    st.session_state.follow_latest = True


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


def format_pct_value(value: Any, already_percent: bool = False, digits: int = 2, signed: bool = False) -> str:
    number = safe_number(value)
    if number is None:
        return "–"
    display = number if already_percent or abs(number) > 1 else number * 100
    sign = "+" if signed and display > 0 else ""
    return f"{sign}{display:.{digits}f}%"


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


def normalize_symbol(symbol: Any, fallback: str = "BTCUSDT") -> str:
    normalized = str(symbol or "").upper().strip()
    return normalized if normalized.endswith("USDT") else fallback


def kline_href(symbol: Any) -> str:
    normalized = normalize_symbol(symbol, str(st.session_state.get("current_symbol", "BTCUSDT")))
    return f"?page=signals&symbol={escape(normalized)}#kline-area"


def kline_symbol_link(symbol: Any, label: str | None = None, css_class: str = "rank-link") -> str:
    normalized = normalize_symbol(symbol, str(st.session_state.get("current_symbol", "BTCUSDT")))
    text = escape(str(label or normalized))
    return f'<a class="{css_class}" href="{kline_href(normalized)}" target="_self">{text}</a>'


def render_kline_jump_links(symbols: list[Any], title: str = "相关币种K线") -> None:
    unique: list[str] = []
    seen: set[str] = set()
    for item in symbols:
        symbol = normalize_symbol(item, "")
        if symbol and symbol not in seen:
            seen.add(symbol)
            unique.append(symbol)
    if not unique:
        return
    links = " ".join(kline_symbol_link(symbol, symbol, "watch-pill") for symbol in unique[:24])
    st.markdown(
        f'<div class="app-shell"><div class="status-card"><b>{escape(title)}</b><br>{links}</div></div>',
        unsafe_allow_html=True,
    )


def inject_styles() -> None:
    st.markdown(
        """
        <style>
          :root { --bg:#050B14; --panel:#0F172A; --panel2:#111827; --border:#1F2937; --border2:#334155; --text:#E5E7EB; --muted:#9CA3AF; --green:#00C087; --red:#F6465D; --yellow:#F0B90B; --blue:#3B82F6; }
          .stApp { background:radial-gradient(circle at 20% 0%, rgba(59,130,246,.12), transparent 28%), var(--bg); color:var(--text); }
          [data-testid="stHeader"] { background:transparent; height:0; } [data-testid="stToolbar"] { display:none; }
          .block-container { padding:48px 8px 82px; max-width:1180px; }
          h1,h2,h3,p,span,div,label { color:var(--text); }
          .app-shell { max-width:1180px; margin:0 auto; }
          .market-ticker { position:fixed; top:0; left:0; right:0; z-index:998; background:rgba(5,11,20,.97); border-bottom:1px solid var(--border2); backdrop-filter:blur(14px); padding:4px 6px; }
          .market-ticker-inner { max-width:1180px; margin:0 auto; display:grid; grid-template-columns:1.12fr 1fr .76fr .78fr .72fr .72fr; gap:4px; }
          .ticker-cell { min-height:29px; border:1px solid var(--border); background:rgba(15,23,42,.92); border-radius:8px; padding:3px 5px; overflow:hidden; }
          .ticker-label { color:var(--muted); font-size:9px; line-height:1; }
          .ticker-value { color:#fff; font-size:11px; font-weight:900; margin-top:2px; white-space:nowrap; }
          .green { color:var(--green)!important; } .red { color:var(--red)!important; } .yellow { color:var(--yellow)!important; } .blue { color:var(--blue)!important; }
          .page-head { display:flex; justify-content:space-between; align-items:flex-start; gap:10px; margin:10px 0 8px; }
          .page-title { font-size:20px; font-weight:900; }
          .page-desc,.module-desc,.small-muted { color:var(--muted); font-size:11px; line-height:1.45; }
          .version-pill,.pending { border:1px solid rgba(240,185,11,.35); color:var(--yellow); background:rgba(240,185,11,.08); border-radius:999px; padding:5px 8px; font-size:11px; font-weight:800; width:max-content; }
          .module-grid { display:grid; grid-template-columns:1fr; gap:8px; }
          .module-card,.kline-card,.list-card { border:1px solid var(--border); background:linear-gradient(180deg, rgba(15,23,42,.96), rgba(17,24,39,.92)); border-radius:14px; padding:10px; }
          .module-card { min-height:78px; }
          .rank-layout { display:grid; grid-template-columns:repeat(3,minmax(0,1fr)); gap:8px; align-items:start; }
          .rank-layout .list-card { min-width:0; padding:8px; }
          .rank-layout .module-title { font-size:13px; }
          .module-title { font-size:15px; font-weight:900; color:#fff; }
          .metric-grid { display:grid; grid-template-columns:repeat(2,minmax(0,1fr)); gap:8px; margin:8px 0; }
          .metric-box { border:1px solid var(--border2); background:rgba(5,11,20,.48); border-radius:10px; padding:7px; min-height:50px; }
          .metric-label { color:var(--muted); font-size:11px; }
          .metric-value { color:#fff; font-size:15px; font-weight:900; margin-top:3px; }
          .terminal-grid { display:grid; grid-template-columns:repeat(4,minmax(0,1fr)); gap:7px; margin:8px 0; }
          .terminal-card { border:1px solid rgba(51,65,85,.72); background:rgba(5,11,20,.50); border-radius:10px; padding:7px; min-height:48px; }
          .terminal-label { color:var(--muted); font-size:10px; line-height:1.1; }
          .terminal-value { color:#fff; font-size:14px; font-weight:900; margin-top:4px; line-height:1.18; overflow-wrap:anywhere; }
          .side-layout { display:grid; grid-template-columns:minmax(0,2.15fr) minmax(220px,.85fr); gap:8px; align-items:stretch; margin-top:8px; }
          .side-stack { display:grid; grid-template-columns:1fr; gap:6px; align-content:start; }
          .summary-card { border:1px solid rgba(51,65,85,.72); background:rgba(15,23,42,.72); border-radius:10px; padding:7px; min-height:44px; }
          .summary-label { color:var(--muted); font-size:10px; }
          .summary-value { color:#fff; font-size:14px; font-weight:900; margin-top:3px; overflow-wrap:anywhere; }
          .committee-summary-panel { border:1px solid var(--border); background:rgba(15,23,42,.74); border-radius:12px; padding:9px; margin-top:8px; }
          .committee-summary-title { color:#fff; font-size:13px; font-weight:900; margin-bottom:6px; }
          .committee-summary-strip { display:grid; grid-template-columns:repeat(3,minmax(0,1fr)); gap:6px; }
          .committee-summary-item { border:1px solid rgba(51,65,85,.72); background:rgba(5,11,20,.38); border-radius:9px; padding:6px; min-height:40px; overflow:hidden; }
          .committee-summary-item .label { color:var(--muted); font-size:9px; line-height:1.1; white-space:nowrap; }
          .committee-summary-item .value { color:#fff; font-size:12px; line-height:1.18; font-weight:900; margin-top:4px; overflow:hidden; text-overflow:ellipsis; white-space:nowrap; }
          .committee-grid { display:grid; grid-template-columns:repeat(3,minmax(0,1fr)); gap:6px; margin-top:8px; }
          .committee-vote-grid { display:grid; grid-template-columns:repeat(2,minmax(0,1fr)); gap:6px; margin-top:8px; }
          .quick-grid { display:grid; grid-template-columns:repeat(2,minmax(0,1fr)); gap:8px; margin-top:8px; }
          .quick-button { border:1px solid var(--border2); background:rgba(5,11,20,.5); border-radius:12px; padding:9px; min-height:46px; display:flex; align-items:center; justify-content:center; text-align:center; font-weight:900; }
          .symbol-panel { border:1px solid var(--border); background:rgba(15,23,42,.82); border-radius:14px; padding:8px; margin:8px 0; }
          .symbol-panel-title { font-size:13px; font-weight:900; color:#fff; margin-bottom:5px; }
          .symbol-row { display:grid; grid-template-columns:1fr auto; gap:8px; align-items:center; }
          .symbol-current { color:#fff; font-size:18px; font-weight:900; }
          .symbol-hint { color:var(--muted); font-size:11px; }
          .rank-list { display:flex; flex-direction:column; gap:0; margin-top:6px; }
          .rank-row { display:grid; grid-template-columns:25px 1.22fr .92fr .7fr .82fr; gap:3px; align-items:center; min-height:22px; border-bottom:1px solid rgba(51,65,85,.28); font-size:9.8px; text-decoration:none; }
          .rank-row:last-child { border-bottom:none; }
          .rank-head { position:sticky; top:38px; z-index:5; color:var(--muted); font-size:9.4px; font-weight:800; background:rgba(15,23,42,.98); border-radius:8px; }
          .rank-index { color:var(--yellow); font-weight:900; }
          .rank-index.gold { color:#F0B90B; } .rank-index.silver { color:#CBD5E1; } .rank-index.bronze { color:#CD7F32; }
          .rank-symbol { font-weight:900; color:#fff; }
          .rank-volume { color:var(--muted); text-align:right; }
          .rank-link { color:inherit; text-decoration:none; }
          .rank-link:hover { background:rgba(240,185,11,.07); border-radius:7px; }
          div[data-testid="stTabs"] [role="tablist"] { overflow-x:auto; flex-wrap:nowrap; gap:4px; border-bottom:1px solid rgba(51,65,85,.55); }
          div[data-testid="stTabs"] [role="tab"] { flex:0 0 auto; white-space:nowrap; color:var(--muted); font-size:12px; font-weight:900; padding:6px 8px; }
          div[data-testid="stTabs"] [aria-selected="true"] { color:var(--yellow)!important; border-bottom-color:var(--yellow)!important; }
          .opp-row { display:grid; grid-template-columns:1.28fr .86fr .66fr .66fr; gap:5px; align-items:center; min-height:34px; border-bottom:1px solid rgba(51,65,85,.28); padding:4px 0; font-size:10.2px; }
          .opp-row.compact-five { grid-template-columns:1.22fr .66fr .68fr .62fr .72fr; gap:4px; min-height:32px; }
          .opp-row:last-child { border-bottom:none; }
          .rank-layout .opp-row { grid-template-columns:1.28fr .78fr .62fr .72fr; gap:3px; min-height:30px; font-size:9.2px; }
          .rank-layout .opp-meta { font-size:8px; }
          .rank-layout .opp-symbol { font-size:9.4px; }
          .opp-symbol { color:#fff; font-weight:900; }
          .opp-meta { color:var(--muted); font-size:9px; line-height:1.35; }
          .score-pill { border:1px solid rgba(59,130,246,.38); background:rgba(59,130,246,.10); color:#93C5FD; border-radius:999px; padding:2px 6px; font-weight:900; width:max-content; }
          .advice-pill { border:1px solid rgba(240,185,11,.34); background:rgba(240,185,11,.08); color:var(--yellow); border-radius:999px; padding:2px 6px; font-weight:900; width:max-content; }
          .watch-pill { display:inline-flex; align-items:center; justify-content:center; min-height:21px; border:1px solid rgba(240,185,11,.38); background:rgba(240,185,11,.08); color:var(--yellow); border-radius:999px; padding:2px 7px; font-size:9px; font-weight:900; text-decoration:none; white-space:nowrap; }
          .watch-pill.done { border-color:rgba(0,192,135,.38); background:rgba(0,192,135,.09); color:var(--green); }
          .watch-info-grid { display:grid; grid-template-columns:repeat(4,minmax(0,1fr)); gap:1px; border:1px solid rgba(51,65,85,.75); border-radius:12px; overflow:hidden; margin-top:8px; background:rgba(5,11,20,.34); }
          .watch-info-cell { min-height:42px; padding:6px; border-right:1px solid rgba(51,65,85,.45); border-bottom:1px solid rgba(51,65,85,.45); }
          .watch-info-cell:nth-child(4n) { border-right:none; }
          .watch-info-label { color:var(--muted); font-size:9px; line-height:1; }
          .watch-info-value { color:#fff; font-size:12px; font-weight:900; margin-top:4px; white-space:nowrap; overflow:hidden; text-overflow:ellipsis; }
          .watch-action-grid { display:grid; grid-template-columns:repeat(4,minmax(0,1fr)); gap:4px; margin:5px 0 8px; }
          .watch-action-grid div[data-testid="stButton"] > button { width:100%; min-height:25px; font-size:10px; padding:1px 3px; }
          div[data-testid="stButton"] > button { min-height:24px; padding:1px 5px; border-radius:7px; border:1px solid rgba(51,65,85,.78); background:rgba(15,23,42,.9); color:#E5E7EB; font-size:10.5px; font-weight:900; line-height:1.1; }
          div[data-testid="stExpander"] { background:#0f172a!important; border:1px solid #24324a!important; border-radius:14px!important; color:#e5e7eb!important; overflow:hidden; }
          div[data-testid="stExpander"] details { background:#0f172a!important; color:#e5e7eb!important; }
          div[data-testid="stExpander"] summary { background:#0f172a!important; color:#e5e7eb!important; border-radius:12px!important; }
          div[data-testid="stExpander"] * { color:#e5e7eb; }
          pre, code { background:#111827!important; color:#e5e7eb!important; border-color:#24324a!important; }
          .status-card { border:1px solid var(--border); background:rgba(15,23,42,.74); border-radius:12px; padding:9px; font-size:12px; line-height:1.6; }
          .error-box { border:1px solid rgba(246,70,93,.42); background:rgba(246,70,93,.12); color:#FCA5A5; border-radius:12px; padding:10px; margin:8px 0; font-size:12px; }
          .kline-head { display:flex; justify-content:space-between; align-items:flex-start; gap:8px; margin-bottom:8px; }
          .kline-title { font-size:16px; font-weight:900; color:#fff; }
          .kline-status { color:var(--muted); font-size:11px; line-height:1.5; text-align:right; }
          .kline-meta-grid { display:grid; grid-template-columns:repeat(2,minmax(0,1fr)); gap:8px; margin-top:8px; }
          .kline-meta-box { border:1px solid var(--border2); background:rgba(15,23,42,.68); border-radius:12px; padding:8px; min-height:56px; }
          .js-plotly-plot .plotly, .js-plotly-plot .main-svg { touch-action:none; }
          .orderbook-card { border:1px solid var(--border); background:linear-gradient(180deg, rgba(15,23,42,.96), rgba(5,11,20,.92)); border-radius:14px; padding:10px; margin:8px 0; }
          .orderbook-head { display:flex; justify-content:space-between; align-items:flex-start; gap:8px; margin-bottom:6px; }
          .orderbook-title { font-size:16px; font-weight:900; color:#fff; }
          .orderbook-status { color:var(--muted); font-size:11px; line-height:1.45; text-align:right; }
          .orderbook-grid { display:grid; grid-template-columns:1fr; gap:6px; }
          .orderbook-table { display:flex; flex-direction:column; gap:0; }
          .orderbook-row { position:relative; display:grid; grid-template-columns:1fr 1fr 1fr; align-items:center; min-height:22px; padding:1px 4px; border-bottom:1px solid rgba(51,65,85,.26); font-size:10px; overflow:hidden; }
          .orderbook-row.header { color:var(--muted); font-weight:800; background:rgba(15,23,42,.8); border-radius:7px; }
          .orderbook-row.large { background:rgba(240,185,11,.08); }
          .depth-bar { position:absolute; top:1px; bottom:1px; right:0; opacity:.18; border-radius:5px; pointer-events:none; }
          .depth-bar.ask { background:var(--red); }
          .depth-bar.bid { background:var(--green); }
          .ob-cell { position:relative; z-index:1; white-space:nowrap; overflow:hidden; text-overflow:ellipsis; }
          .ob-right { text-align:right; }
          .last-price-box { border:1px solid var(--border2); border-radius:12px; padding:8px; margin:6px 0; text-align:center; background:rgba(5,11,20,.52); }
          .last-price { font-size:22px; font-weight:900; color:#fff; }
          .ratio-bar { display:grid; grid-template-columns:1fr 1fr; height:8px; overflow:hidden; border-radius:999px; background:rgba(148,163,184,.18); margin:7px 0; }
          .ratio-buy { background:var(--green); }
          .ratio-sell { background:var(--red); }
          .orderbook-summary { display:grid; grid-template-columns:repeat(2,minmax(0,1fr)); gap:6px; margin-top:8px; }
          .orderbook-summary .metric-box { min-height:48px; padding:6px; }
          .bottom-nav { position:fixed; left:0; right:0; bottom:0; z-index:999; background:rgba(5,11,20,.96); border-top:1px solid var(--border2); backdrop-filter:blur(14px); padding:5px 6px 6px; overflow-x:auto; scrollbar-width:none; }
          .bottom-nav::-webkit-scrollbar { display:none; }
          .bottom-nav-inner { max-width:1160px; min-width:max-content; margin:0 auto; display:flex; flex-wrap:nowrap; gap:3px; }
          .nav-item { flex:0 0 54px; display:flex; flex-direction:column; align-items:center; justify-content:center; gap:2px; min-height:46px; border-radius:10px; color:var(--muted); font-size:9.5px; line-height:1; border:1px solid transparent; text-decoration:none; white-space:nowrap; }
          .nav-item.active { color:var(--yellow); background:rgba(240,185,11,.09); border-color:rgba(240,185,11,.28); } .nav-icon { font-size:15px; line-height:1; }
          @media (min-width:720px) { .block-container { padding:52px 18px 96px; } .module-grid { grid-template-columns:repeat(2,minmax(0,1fr)); } .metric-grid,.kline-meta-grid { grid-template-columns:repeat(4,minmax(0,1fr)); } }
          @media (min-width:900px) { .orderbook-grid { grid-template-columns:1fr 1fr; } }
          @media (min-width:1100px) { .module-grid { grid-template-columns:repeat(3,minmax(0,1fr)); } }
          @media (max-width:900px) { .side-layout { grid-template-columns:1fr; } .side-stack { grid-template-columns:repeat(2,minmax(0,1fr)); } .terminal-grid,.committee-grid,.committee-summary-strip { grid-template-columns:repeat(2,minmax(0,1fr)); } .committee-vote-grid { grid-template-columns:1fr; } }
          @media (max-width:430px) { .market-ticker-inner { grid-template-columns:1.08fr .98fr .72fr .72fr .66fr .66fr; gap:3px; } .ticker-cell { min-height:27px; padding:3px 4px; } .ticker-label { font-size:8px; } .ticker-value { font-size:10px; } .rank-row { grid-template-columns:22px 1.15fr .83fr .66fr .76fr; min-height:21px; font-size:9px; } .opp-row.compact-five { grid-template-columns:1.05fr .6fr .64fr .58fr .62fr; gap:2px; font-size:9px; } .watch-pill { font-size:8px; padding:2px 5px; } .watch-info-grid { grid-template-columns:repeat(2,minmax(0,1fr)); } .watch-info-cell:nth-child(2n) { border-right:none; } .watch-action-grid { grid-template-columns:repeat(4,minmax(0,1fr)); gap:3px; } .watch-action-grid div[data-testid="stButton"] > button { font-size:9px; } .terminal-grid,.side-stack,.committee-grid,.committee-summary-strip { grid-template-columns:repeat(2,minmax(0,1fr)); gap:5px; } .terminal-card,.summary-card,.committee-summary-item { padding:6px; min-height:40px; } .terminal-value,.summary-value,.committee-summary-item .value { font-size:12px; } .bottom-nav { padding:4px 4px 5px; } .bottom-nav-inner { gap:2px; } .nav-item { flex-basis:42px; min-height:40px; border-radius:8px; font-size:7.5px; } .nav-icon { font-size:12px; } }
        </style>
        """,
        unsafe_allow_html=True,
    )


def calculate_ma(values: list[float], window: int) -> list[float | None]:
    """计算简单移动平均线。"""
    result: list[float | None] = []
    rolling_sum = 0.0
    for index, value in enumerate(values):
        rolling_sum += value
        if index >= window:
            rolling_sum -= values[index - window]
        result.append(rolling_sum / window if index + 1 >= window else None)
    return result


def local_scores(ticker: dict[str, Any] | None, rows: list[dict[str, Any]]) -> dict[str, Any]:
    """根据本地行情与K线计算趋势、风险、机会评分。"""
    default = {"trend_label": "计算中", "trend_score": None, "risk_score": None, "opportunity_score": None}
    if not ticker or len(rows) < 60:
        return default
    closes = [row["close"] for row in rows]
    close = closes[-1]
    ma20 = calculate_ma(closes, 20)[-1]
    ma60 = calculate_ma(closes, 60)[-1]
    change = ticker["price_change_percent"]
    recent_high = max(row["high"] for row in rows[-40:])
    recent_low = min(row["low"] for row in rows[-40:])
    volatility = ((recent_high - recent_low) / close) * 100 if close else 0

    trend_score = 50
    if ma20 and ma60:
        if close > ma20 > ma60:
            trend_score += 28
        elif close < ma20 < ma60:
            trend_score -= 28
        elif close > ma20:
            trend_score += 10
        elif close < ma20:
            trend_score -= 10
    trend_score += min(12, max(-12, change * 2))
    trend_score = int(max(0, min(100, trend_score)))

    risk_score = int(max(0, min(100, volatility * 6 + max(0, abs(change) - 3) * 5)))
    opportunity_score = int(max(0, min(100, trend_score * 0.72 + (100 - risk_score) * 0.28)))

    if trend_score >= 82 and change >= 0:
        trend_label = "强势上涨"
    elif trend_score >= 62:
        trend_label = "上涨"
    elif trend_score <= 18 and change <= 0:
        trend_label = "强势下跌"
    elif trend_score <= 38:
        trend_label = "下跌"
    else:
        trend_label = "震荡"
    return {"trend_label": trend_label, "trend_score": trend_score, "risk_score": risk_score, "opportunity_score": opportunity_score}


def score_text(value: Any, loading: str = "计算中") -> str:
    """区分未计算和真实0分。"""
    if value is None:
        return loading
    return str(value)


def score_color(value: Any, good_at: int, warn_at: int | None = None) -> str:
    """评分卡片颜色，未计算时使用黄色加载态。"""
    if value is None:
        return "yellow"
    try:
        number = float(value)
    except (TypeError, ValueError):
        return "yellow"
    if warn_at is not None and number >= warn_at:
        return "red"
    return "green" if number >= good_at else "yellow"


def build_ai_status(scores: dict[str, Any]) -> tuple[str, str]:
    """首页AI建议状态，避免把已开发模块显示成待接入。"""
    opportunity = safe_score(scores.get("opportunity_score"))
    risk = safe_score(scores.get("risk_score"))
    if opportunity is None or risk is None:
        return "计算中", "yellow"
    if risk >= 85:
        return "风控禁止", "red"
    if opportunity >= 80 and risk < 65:
        return "候选复核", "green"
    if opportunity >= 70:
        return "重点观察", "yellow"
    return "继续观察", "blue"


def detect_cross(rows: list[dict[str, Any]], ma_short: list[float | None], ma_long: list[float | None]) -> dict[str, Any]:
    """识别最近一次均线金叉或死叉。"""
    for index in range(len(rows) - 1, 0, -1):
        prev_short, prev_long = ma_short[index - 1], ma_long[index - 1]
        curr_short, curr_long = ma_short[index], ma_long[index]
        if None in (prev_short, prev_long, curr_short, curr_long):
            continue
        if prev_short <= prev_long and curr_short > curr_long:
            return {"type": "golden", "label": "金叉", "time": rows[index]["open_datetime"], "price": rows[index]["close"]}
        if prev_short >= prev_long and curr_short < curr_long:
            return {"type": "death", "label": "死叉", "time": rows[index]["open_datetime"], "price": rows[index]["close"]}
    return {"type": "none", "label": "无明显交叉", "time": None, "price": None}


def detect_support_resistance(rows: list[dict[str, Any]]) -> tuple[float | None, float | None]:
    """基于最近 K线高低点估算关键支撑与压力。"""
    if len(rows) < 20:
        return None, None
    recent = rows[-120:]
    current = recent[-1]["close"]
    lows = [row["low"] for row in recent]
    highs = [row["high"] for row in recent]
    support_candidates = sorted({price for price in lows if price <= current}, reverse=True)
    resistance_candidates = sorted({price for price in highs if price >= current})
    support = support_candidates[0] if support_candidates else min(lows)
    resistance = resistance_candidates[0] if resistance_candidates else max(highs)
    return support, resistance


def analyze_kline_state(rows: list[dict[str, Any]], ma20: list[float | None], ma60: list[float | None]) -> str:
    """输出简洁 K线状态。"""
    if len(rows) < 80 or ma20[-1] is None or ma60[-1] is None:
        return "数据积累中"
    close = rows[-1]["close"]
    recent_high = max(row["high"] for row in rows[-31:-1])
    recent_low = min(row["low"] for row in rows[-31:-1])
    previous_close = rows[-2]["close"]
    if close > recent_high:
        return "突破"
    if previous_close > recent_high and close < recent_high:
        return "假突破"
    if close < recent_low:
        return "下跌趋势"
    if close > ma20[-1] > ma60[-1]:
        return "上涨趋势"
    if close < ma20[-1] < ma60[-1]:
        return "下跌趋势"
    if abs(close - ma20[-1]) / close < 0.01:
        return "回踩"
    return "震荡整理"


def _fragment_1s(func):
    """启用 Streamlit 1秒局部刷新，失败时降级为普通渲染。"""
    fragment = getattr(st, "fragment", None) or getattr(st, "experimental_fragment", None)
    if fragment:
        try:
            return fragment(run_every="1s")(func)
        except Exception as exc:
            print(f"[AI模型7.1.2] 局部刷新启用失败 func={func.__name__} error={repr(exc)}")
    return func


def _find_opportunity_row(symbol: str, rankings: dict[str, list[dict[str, Any]]] | None = None) -> dict[str, Any] | None:
    """从实时机会榜中按 symbol 找行，供顶部栏和委员会显示共用。"""
    live_symbol = str(symbol or "").upper().strip()
    if not live_symbol:
        return None
    for row in _combined_trade_opportunities(rankings or market_cache.get_rankings(), 10):
        if str(row.get("symbol", "")).upper() == live_symbol:
            return row
    for row in st.session_state.get("opportunity_board", []) or []:
        if str(row.get("symbol", "")).upper() == live_symbol:
            return row
    return None


def live_refresh_due(key: str, seconds: float) -> bool:
    """Session-local throttle for live server-side refreshes."""
    now = time.monotonic()
    refresh_times = st.session_state.setdefault("_live_refresh_times", {})
    last = float(refresh_times.get(key, 0) or 0)
    if now - last < seconds:
        return False
    refresh_times[key] = now
    return True


def ticker_from_rankings(symbol: str, rankings: dict[str, list[dict[str, Any]]] | None = None) -> dict[str, Any] | None:
    """Build a lightweight ticker from ranking rows when live ticker cache is cold."""
    row = _find_opportunity_row(symbol, rankings)
    if not row:
        return None
    price = safe_number(row.get("last_price"), safe_number(row.get("current_price"), safe_number(row.get("price"))))
    if price is None or price <= 0:
        return None
    return {
        "symbol": str(row.get("symbol") or symbol).upper().strip(),
        "last_price": price,
        "price_change_percent": safe_number(row.get("price_change_percent"), 0) or 0,
        "quote_volume": safe_number(row.get("quote_volume"), 0) or 0,
        "volume": safe_number(row.get("volume"), 0) or 0,
        "updated_at": row.get("updated_at") or "来自机会榜缓存",
        "price_status": "ranking",
    }


def get_effective_ticker(symbol: str, rankings: dict[str, list[dict[str, Any]]] | None = None) -> dict[str, Any] | None:
    """Read live ticker first, then bridge from rankings if needed."""
    normalized = str(symbol or "").upper().strip()
    ticker = market_cache.get_ticker(normalized)
    if ticker:
        ticker.setdefault("price_status", "live")
        return ticker
    fallback = ticker_from_rankings(normalized, rankings)
    if fallback:
        market_cache.set_ticker(normalized, fallback)
        append_debug_log(SIGNAL_CHAIN_LOG, f"ticker_bridge_from_rankings symbol={normalized}")
        return fallback
    return None


def render_fixed_market_bar(symbol: str) -> None:
    """渲染前端实时顶部状态栏，不触发 Streamlit 每秒重绘。"""
    try:
        live_symbol = str(st.session_state.get("current_symbol") or symbol or "BTCUSDT").upper().strip()
        interval = market_cache.get_kline_interval()
        rankings = market_cache.get_rankings()
        ticker = get_effective_ticker(live_symbol, rankings)
        if not ticker:
            try:
                ticker = get_24hr_ticker(live_symbol)
                market_cache.set_ticker(live_symbol, ticker)
            except Exception:
                ticker = None
        kline_rows = market_cache.get_klines(live_symbol, interval)
        orderbook = market_cache.get_orderbook(live_symbol)
        whale = market_cache.get_whales(live_symbol)
        scores = local_scores(ticker, kline_rows) or {}
        opportunity_row = _find_opportunity_row(live_symbol, rankings)
        committee_summary = (opportunity_row or {}).get("committee_summary") or {}
        final_opp = (opportunity_row or {}).get("final_opportunity_score", scores.get("opportunity_score"))
        risk_value = (opportunity_row or {}).get("risk_score", scores.get("risk_score"))
        status_bits = []
        if not ticker:
            status_bits.append("等待实时价格")
        if len(kline_rows) < 80:
            status_bits.append(f"K线{len(kline_rows)}/80")
        if not orderbook:
            status_bits.append("盘口等待刷新")
        if not whale:
            status_bits.append("暂无明显大单")
        data_status = "｜".join(status_bits) or "数据实时"
        ai_advice = committee_summary.get("final_action") or scores.get("trend_label") or data_status
        update_time = escape(str((ticker or {}).get("updated_at") or market_cache.snapshot().get("last_update_time") or "等待更新"))
        price = format_price((ticker or {}).get("last_price"))
        change_number = safe_number((ticker or {}).get("price_change_percent"))
        change = format_percent(change_number)
        change_class = "green" if (change_number is not None and change_number >= 0) else "red" if change_number is not None else "yellow"
        risk_class = get_risk_class(risk_value)
        opp_class = get_opportunity_class(final_opp)
    except Exception as exc:
        live_symbol = str(symbol or "BTCUSDT")
        price = "顶部行情栏渲染失败"
        change = "重试中"
        change_class = risk_class = opp_class = "yellow"
        ai_advice = f"获取失败：{exc!r}"
        risk_value = None
        final_opp = None
    html = f"""
    <style>
      body {{ margin:0; background:transparent; font-family:Arial,'Microsoft YaHei',sans-serif; }}
      .market-ticker {{ position:fixed; top:0; left:0; right:0; z-index:9999; background:rgba(5,11,20,.98); border-bottom:1px solid #1F2937; padding:4px 6px; }}
      .market-ticker-inner {{ display:grid; grid-template-columns:1.02fr .9fr .66fr .78fr .62fr .62fr; gap:4px; max-width:1180px; margin:0 auto; }}
      .ticker-cell {{ min-height:29px; border:1px solid #1F2937; background:rgba(15,23,42,.92); border-radius:8px; padding:3px 5px; overflow:hidden; }}
      .ticker-label {{ color:#9CA3AF; font-size:8px; line-height:1.1; white-space:nowrap; }}
      .ticker-value {{ color:#E5E7EB; font-weight:900; font-size:10px; line-height:1.3; white-space:nowrap; overflow:hidden; text-overflow:ellipsis; }}
      .green {{ color:#00C087 !important; }} .red {{ color:#F6465D !important; }} .yellow {{ color:#F0B90B !important; }} .blue {{ color:#3B82F6 !important; }}
    </style>
    <div class="market-ticker"><div class="market-ticker-inner">
      <div class="ticker-cell"><div class="ticker-label">当前交易对象</div><div class="ticker-value" id="symbol">{live_symbol}</div></div>
      <div class="ticker-cell"><div class="ticker-label">当前价格</div><div class="ticker-value" id="price">{price}</div></div>
      <div class="ticker-cell"><div class="ticker-label">涨跌幅</div><div class="ticker-value {change_class}" id="change">{change}</div></div>
      <div class="ticker-cell"><div class="ticker-label">AI建议 / 状态</div><div class="ticker-value yellow" title="{escape(data_status)}｜更新：{update_time}">{escape(str(ai_advice))}</div></div>
      <div class="ticker-cell"><div class="ticker-label">风险评分</div><div class="ticker-value {risk_class}">{format_score(risk_value)}</div></div>
      <div class="ticker-cell"><div class="ticker-label">机会评分</div><div class="ticker-value {opp_class}">{format_score(final_opp)}</div></div>
    </div></div>
    <script>
      const symbol = "{live_symbol}";
      const priceEl = document.getElementById("price");
      const changeEl = document.getElementById("change");
      {frontend_api_client_js("fetchTopbarJson")}
      function fmtPrice(v) {{
        const n = Number(v);
        if (!Number.isFinite(n)) return "正在获取";
        if (n >= 1000) return n.toLocaleString(undefined, {{minimumFractionDigits:2, maximumFractionDigits:2}});
        if (n >= 1) return n.toLocaleString(undefined, {{minimumFractionDigits:4, maximumFractionDigits:4}});
        return n.toFixed(8).replace(/0+$/,'').replace(/\\.$/,'');
      }}
      async function tick() {{
        try {{
          const data = await fetchTopbarJson(`/api/ticker?symbol=${{encodeURIComponent(symbol)}}`);
          const change = Number(data.price_change_percent);
          priceEl.textContent = fmtPrice(data.last_price);
          changeEl.textContent = Number.isFinite(change) ? `${{change > 0 ? "+" : ""}}${{change.toFixed(2)}}%` : "重试中";
          changeEl.className = Number.isFinite(change) ? `ticker-value ${{change >= 0 ? "green" : "red"}}` : "ticker-value yellow";
        }} catch (err) {{
          priceEl.textContent = err && err.message ? err.message : "获取失败";
          changeEl.textContent = "等待刷新";
          changeEl.className = "ticker-value yellow";
        }}
      }}
      tick();
      setInterval(tick, 1000);
    </script>
    """
    components.html(html, height=43, scrolling=False)


def frontend_api_client_js(function_name: str = "fetchLocalApiJson") -> str:
    """生成浏览器端本地行情 API 客户端，兼容本机和远程服务器访问。"""
    port = str(get_local_api_port())
    return (
        """
      const LOCAL_API_PORT = "__PORT__";
      function getCleanLocalApiBase() {
        let protocol = "";
        let hostname = "";
        const readLocation = (loc) => {
          try {
            if (!protocol && /^https?:$/.test(loc.protocol || "")) protocol = loc.protocol;
            if (!hostname && loc.hostname) hostname = loc.hostname;
          } catch (_) {}
        };
        readLocation(window.location);
        try {
          if (window.parent && window.parent !== window) readLocation(window.parent.location);
        } catch (_) {}
        if ((!hostname || !/^https?:$/.test(protocol)) && document.referrer) {
          try {
            const ref = new URL(document.referrer);
            if (!protocol && /^https?:$/.test(ref.protocol || "")) protocol = ref.protocol;
            if (!hostname && ref.hostname) hostname = ref.hostname;
          } catch (_) {}
        }
        if (!/^https?:$/.test(protocol)) protocol = "http:";
        if (!hostname) throw new Error("无法识别当前公网主机，前端行情API地址生成失败");
        return `${protocol}//${hostname}:${LOCAL_API_PORT}`;
      }
      function buildLocalApiUrl(path) {
        const apiBase = getCleanLocalApiBase();
        const parsed = new URL(String(path || "/"), apiBase);
        const url = new URL(`${parsed.pathname}${parsed.search}`, apiBase);
        url.username = "";
        url.password = "";
        return url.toString();
      }
      async function __FN__(path) {
        try {
          const res = await fetch(buildLocalApiUrl(path), {cache:"no-store"});
          let data = {};
          try { data = await res.json(); } catch (_) { data = {}; }
          if (res.ok && data.ok !== false) return data;
          throw new Error(data.message || data.error || `HTTP ${res.status}`);
        } catch (err) {
          const message = err && err.message ? err.message : "本地行情API不可用";
          if (/URL is not valid|user credentials/i.test(message)) {
            throw new Error("前端行情API地址无效，请检查公网访问地址和API端口");
          }
          throw new Error(message);
        }
      }
        """
        .replace("__PORT__", port)
        .replace("__FN__", function_name)
    )


def render_page_head(page_key: str) -> None:
    """渲染页面标题。"""
    title, desc = PAGE_TITLES[page_key]
    st.markdown(
        f'<div class="app-shell"><div class="page-head"><div><div class="page-title">{title}</div><div class="page-desc">{desc}</div></div><div class="version-pill">{VERSION}</div></div></div>',
        unsafe_allow_html=True,
    )


def render_metric_grid(items: list[tuple[str, str, str]]) -> None:
    """渲染紧凑指标卡片。"""
    html = ['<div class="app-shell"><div class="metric-grid">']
    for label, value, klass in items:
        html.append(f'<div class="metric-box"><div class="metric-label">{label}</div><div class="metric-value {klass}">{value}</div></div>')
    html.append("</div></div>")
    st.markdown("".join(html), unsafe_allow_html=True)


def filter_symbols(query: str, symbols: list[str]) -> list[str]:
    """大小写不敏感的 USDT 交易对模糊搜索，优先精确和前缀匹配。"""
    clean_symbols = [str(symbol).upper().strip() for symbol in symbols if str(symbol).upper().strip().endswith("USDT")]
    seen: set[str] = set()
    unique_symbols = []
    for symbol in clean_symbols:
        if symbol not in seen:
            seen.add(symbol)
            unique_symbols.append(symbol)
    q = str(query or "").upper().strip()
    if not q:
        return unique_symbols[:50]
    exact = [symbol for symbol in unique_symbols if symbol == q or symbol.replace("USDT", "") == q]
    prefix = [symbol for symbol in unique_symbols if symbol.startswith(q) and symbol not in exact]
    contains = [symbol for symbol in unique_symbols if q in symbol and symbol not in exact and symbol not in prefix]
    return (exact + prefix + contains)[:50]


def render_global_symbol_selector(location: str = "overview", symbols: list[str] | None = None) -> None:
    """全站统一交易对象选择器，选择后同步 current_symbol 和 URL。"""
    available = symbols or market_cache.get_symbols(FALLBACK_SYMBOLS)
    current = str(st.session_state.get("current_symbol") or "BTCUSDT").upper().strip()
    query_key = f"{location}_symbol_search_query"
    select_key = f"{location}_symbol_selector"
    st.markdown(
        f"""<div class="app-shell"><div class="symbol-panel">
        <div class="symbol-row"><div><div class="symbol-panel-title">当前交易对象</div><div class="symbol-current">{escape(current)}</div><div class="symbol-hint">可输入 BTC、ETH、SOL、PEPE 等关键词搜索，切换后全站同步。</div></div></div>
        </div></div>""",
        unsafe_allow_html=True,
    )
    popover = getattr(st, "popover", None)
    container = popover(f"{current} ▼", use_container_width=True) if popover else st.expander(f"{current} ▼", expanded=False)
    with container:
        search = st.text_input("搜索交易对象", key=query_key, placeholder="输入 BTC / ETH / SOL / 1000 / PEPE")
        filtered = filter_symbols(search, available)
        if current and current not in filtered:
            filtered = [current] + filtered
        if not filtered:
            st.caption("没有匹配的交易对象")
            return
        st.caption(f"显示前 {min(len(filtered), 50)} 个匹配交易对")
        selected = st.selectbox(
            "选择交易对象",
            filtered,
            index=filtered.index(current) if current in filtered else 0,
            key=select_key,
        )
        if selected and selected != current:
            set_current_symbol(selected, source=f"{location}_search")
            st.rerun()


def render_symbol_search_panel(symbols: list[str], key_prefix: str) -> None:
    """兼容旧入口的全局交易对象搜索选择器。"""
    render_global_symbol_selector(key_prefix, symbols)


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
    experience_version = get_selected_experience_library_version()
    experience_mode = get_selected_experience_mode()
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
    market_cognition = build_market_cognition(
        symbol=live_symbol,
        ticker=live_ticker,
        rows=rows,
        derivatives=derivatives,
        orderbook_analysis=orderbook_analysis,
        whale=whale,
        signal_analysis=analysis,
        local_strategy=strategy,
        interval_base=interval,
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
        market_cognition=market_cognition,
        experience_mode=experience_mode,
        experience_library_version=experience_version,
        experience_library_path=EXPERIENCE_LIBRARY_VERSIONS.get(experience_version, ""),
        experience_library_data_sources=(
            "current + funding_v1 + oi_longshort_recent30_v1"
            if experience_mode == "fused"
            else get_experience_library_data_sources(experience_version)
        ),
    )


def render_committee_overview_window(decision: dict[str, Any]) -> None:
    """总览页委员会精简窗口。"""
    if not decision:
        return
    v91 = decision.get("trading_committee_v91") or {}
    v91_risk = v91.get("risk_judge") or {}
    v91_position = v91.get("position_plan") or {}
    v91_execution = v91.get("execution_plan") or {}
    permission = _committee_permission_text(str(decision.get("trade_permission", "rejected")))
    action = str(decision.get("final_action", "继续观察"))
    direction = str(decision.get("final_direction_text", "中性"))
    simulation_text = "是" if decision.get("approved_for_simulation") else "否"
    supporting = list(decision.get("supporting_members") or [])
    opposing = list(decision.get("opposing_members") or [])
    veto_members = list(decision.get("veto_members") or [])
    experience_library = decision.get("experience_library") or {}
    committee_experience_mode = str(experience_library.get("mode") or get_selected_experience_mode())
    committee_experience_version = str(experience_library.get("version") or get_selected_experience_library_version())
    committee_experience_sources = str(experience_library.get("data_sources") or get_experience_library_data_sources(committee_experience_version))
    committee_experience_path = str(experience_library.get("path") or EXPERIENCE_LIBRARY_VERSIONS.get(committee_experience_version, ""))
    weight_summary = _committee_weight_summary(decision)
    vote_weight_text = (
        f"支持{_fmt_weight(weight_summary['support_weight'])} / "
        f"观望{_fmt_weight(weight_summary['neutral_weight'])} / "
        f"反对{_fmt_weight(weight_summary['oppose_weight'] + weight_summary['veto_weight'])}"
    )
    vote_cards = []
    for member in list(decision.get("member_votes") or [])[:8]:
        reasons = list(member.get("reasons") or [])
        risks = list(member.get("risks") or [])
        reason = reasons[0] if reasons else (risks[0] if risks else "等待更多数据确认。")
        member_name = str(member.get("member_name", "委员"))
        member_weight = _committee_member_weight(member_name, dict(decision.get("committee_weights") or {}))
        member_bucket = _committee_vote_bucket(member)
        member_type = "影子" if member.get("shadow") else "正式"
        vote_cards.append(
            f"""<div class="summary-card">
              <div class="summary-label">{escape(member_name)} · {member_type} · 权重{_fmt_weight(member_weight)}</div>
              <div class="summary-value {_signal_color(str(member.get("vote", "")))}">{escape(str(member.get("vote", "建议观望")))}</div>
              <div class="module-desc">计入：{escape(member_bucket)}｜方向：{escape(str(member.get("direction_text", "中性")))}｜信心：{member.get("confidence", 0)}｜加权：{escape(str(member.get("weighted_score", 0)))}｜否决：{"是" if member.get("veto") else "否"}</div>
              <div class="module-desc">理由：{escape(_safe_committee_text(reason))}</div>
            </div>"""
        )
    veto_status = "已触发" if veto_members else "未触发"
    render_html(
        f"""
        <div class="app-shell">
          <div class="module-card">
            <div class="module-title">交易委员会</div>
            <div class="metric-value {_signal_color(action)}">{escape(action)} · {escape(direction)}</div>
            <div class="committee-grid">
              <div class="summary-card"><div class="summary-label">当前交易对象</div><div class="summary-value yellow">{escape(str(decision.get("symbol", "-")))}</div></div>
              <div class="summary-card"><div class="summary-label">交易许可</div><div class="summary-value {_signal_color(action)}">{escape(permission)}</div></div>
              <div class="summary-card"><div class="summary-label">共振等级</div><div class="summary-value yellow">{escape(str(decision.get("resonance_text", "无共振")))}</div></div>
              <div class="summary-card"><div class="summary-label">委员会置信度</div><div class="summary-value blue">{decision.get("committee_confidence", 0)} / 100</div></div>
              <div class="summary-card"><div class="summary-label">委员会风险</div><div class="summary-value {_signal_color(str(decision.get("committee_risk_score", 0)))}">{decision.get("committee_risk_score", 0)} / 100</div></div>
              <div class="summary-card"><div class="summary-label">建议仓位</div><div class="summary-value yellow">{escape(str(decision.get("position_suggestion", "0%")))}</div></div>
              <div class="summary-card"><div class="summary-label">模拟候选</div><div class="summary-value {_signal_color("支持交易" if decision.get("approved_for_simulation") else "反对交易")}">{simulation_text}</div></div>
              <div class="summary-card"><div class="summary-label">风险否决</div><div class="summary-value {_signal_color("禁止开仓" if veto_members else "支持交易")}">{veto_status}</div></div>
              <div class="summary-card"><div class="summary-label">投票统计</div><div class="summary-value">支持{len(supporting)} / 反对{len(opposing)} / 否决{len(veto_members)}</div></div>
              <div class="summary-card"><div class="summary-label">权重投票</div><div class="summary-value yellow">{vote_weight_text}</div></div>
              <div class="summary-card"><div class="summary-label">影子参考</div><div class="summary-value blue">{_fmt_weight(weight_summary['shadow_weight'])}</div></div>
              <div class="summary-card"><div class="summary-label">9.1交易结论</div><div class="summary-value {_signal_color(str(v91.get("final_action", "WAIT")))}">{escape(str(v91.get("final_action", "WAIT")))} / {escape(str(v91.get("final_direction", "WAIT")))}</div></div>
              <div class="summary-card"><div class="summary-label">风险裁判</div><div class="summary-value {_signal_color("禁止开仓" if v91_risk.get("blocked") else "支持交易")}">{escape(str(v91_risk.get("risk_verdict", "WAIT")))}</div></div>
              <div class="summary-card"><div class="summary-label">仓位委员会</div><div class="summary-value yellow">{float(v91_position.get("position_size_pct", 0) or 0):.2f}% / {int(v91_position.get("leverage", 1) or 1)}x</div></div>
              <div class="summary-card"><div class="summary-label">执行委员会</div><div class="summary-value {_signal_color("支持交易" if v91_execution.get("execution_allowed") else "反对交易")}">{escape(str(v91_execution.get("execution_type", "WAIT")))}</div></div>
              <div class="summary-card"><div class="summary-label">经验库模式</div><div class="summary-value blue">{escape("融合模式" if committee_experience_mode == "fused" else "单库模式")}</div><div class="module-desc">{escape(committee_experience_version)}｜{escape(committee_experience_sources)}｜经验委员参与正式投票</div></div>
            </div>
            <div class="status-card" style="margin-top:8px;"><b>经验库路径</b><br>{escape(committee_experience_path or "-")}</div>
            {_render_committee_summary_panel(decision)}
            <details class="status-card" style="margin-top:8px;">
              <summary><b>查看委员意见</b></summary>
              <div class="committee-vote-grid">{"".join(vote_cards)}</div>
            </details>
            <a class="watch-pill" href="?page=signals&symbol={escape(str(decision.get("symbol", "")))}" target="_self" style="margin-top:8px;">查看完整委员会详情</a>
          </div>
        </div>
        """
    )
    render_committee_full_summary_expander(decision, "查看完整总结")


def committee_decision_to_sim_signal(decision: dict[str, Any]) -> dict[str, Any]:
    """把当前委员会决议转换为模拟交易信号。"""
    if not decision:
        return {}
    market_cognition = decision.get("market_cognition") if isinstance(decision.get("market_cognition"), dict) else {}
    experience_library = decision.get("experience_library") if isinstance(decision.get("experience_library"), dict) else {}
    experience_mode = str(experience_library.get("mode") or get_selected_experience_mode())
    experience_version = str(experience_library.get("version") or get_selected_experience_library_version())
    experience_match: dict[str, Any] = {}
    if experience_mode == "fused" and isinstance(experience_library.get("fused_experience_result"), dict):
        experience_match = experience_library.get("fused_experience_result") or {}
    elif isinstance(experience_library.get("experience_match_result"), dict) and experience_library.get("experience_match_result"):
        experience_match = experience_library.get("experience_match_result") or {}
    elif market_cognition:
        try:
            query = build_experience_query_from_cognition(str(decision.get("symbol") or ""), market_cognition)
            experience_match = match_experience(query, experience_version=experience_version, top_k=50)
        except Exception as exc:
            experience_match = {
                "available": False,
                "matched": False,
                "vote": "ABSTAIN",
                "direction": "WAIT",
                "confidence": 0,
                "reason": f"模拟信号生成时经验匹配失败：{exc!r}",
                "experience_library_version": experience_version,
            }
    return {
        "symbol": decision.get("symbol"),
        "direction": decision.get("final_direction"),
        "action": decision.get("final_action"),
        "trade_permission": decision.get("trade_permission"),
        "approved_for_simulation": decision.get("approved_for_simulation"),
        "veto_members": decision.get("veto_members"),
        "committee_confidence": decision.get("committee_confidence"),
        "risk_score": decision.get("committee_risk_score"),
        "position_suggestion": decision.get("position_suggestion"),
        "entry_zone": decision.get("entry_zone"),
        "stop_loss": decision.get("stop_loss"),
        "take_profit_1": decision.get("take_profit_1"),
        "take_profit_2": decision.get("take_profit_2"),
        "risk_reward_ratio": decision.get("risk_reward_ratio"),
        "invalid_condition": decision.get("invalid_condition"),
        "chairman_summary": decision.get("chairman_summary"),
        "approved_time": decision.get("timestamp"),
        "main_reasons": decision.get("main_reasons"),
        "main_risks": decision.get("main_risks"),
        "member_votes": decision.get("member_votes"),
        "external_ai": decision.get("external_ai"),
        "external_ai_snapshot": decision.get("external_ai_snapshot") or decision.get("external_ai"),
        "committee_weights": decision.get("committee_weights"),
        "hard_veto_status": decision.get("hard_veto_status"),
        "soft_veto_status": decision.get("soft_veto_status"),
        "system_position_suggestion": decision.get("system_position_suggestion"),
        "risk_max_position": decision.get("risk_max_position"),
        "manual_override_allowed": decision.get("manual_override_allowed"),
        "market_cognition": market_cognition,
        "experience_library": experience_library,
        "experience_mode": experience_mode,
        "experience_library_version": experience_version,
        "experience_match": experience_match,
        "trading_committee_v91": decision.get("trading_committee_v91"),
        "vote_detail": decision.get("vote_detail"),
    }


def build_sim_price_map(current_symbol: str, summary: dict[str, Any] | None = None) -> dict[str, float]:
    """收集模拟交易需要的当前价格。"""
    symbols = {str(current_symbol or st.session_state.get("current_symbol", "BTCUSDT")).upper()}
    summary = summary or get_sim_account_summary()
    rows = list(summary.get("positions") or []) + list(summary.get("orders") or [])
    for row in list(summary.get("positions") or []) + list(summary.get("orders") or []):
        symbol = str(row.get("symbol", "")).upper()
        if symbol:
            symbols.add(symbol)
    prices: dict[str, float] = {}
    for symbol in symbols:
        ticker = market_cache.get_ticker(symbol) or {}
        price = valid_price(ticker.get("last_price"))
        if price is None:
            old = next((row for row in rows if str(row.get("symbol") or "").upper() == symbol), {})
            price = valid_price(old.get("current_price"))
        if price is not None:
            prices[symbol] = price
    return prices


def refresh_sim_positions_lightweight(summary: dict[str, Any] | None = None) -> dict[str, Any]:
    """Refresh only simulated position/order symbols before rendering positions."""
    summary = summary or get_sim_account_summary()
    rows = list(summary.get("positions") or []) + list(summary.get("orders") or [])
    symbols = sorted({str(row.get("symbol") or "").upper().strip() for row in rows if row.get("symbol")})
    price_map: dict[str, float] = {}
    statuses: dict[str, str] = {}
    success = 0
    missing = 0
    for symbol in symbols:
        ticker = market_cache.get_ticker(symbol)
        if not ticker:
            try:
                refresh_symbol_now(symbol)
                ticker = market_cache.get_ticker(symbol)
            except Exception as exc:
                append_debug_log(POSITION_PRICE_LOG, f"position_page_refresh_failed symbol={symbol} error={repr(exc)}")
        price = safe_number((ticker or {}).get("last_price"), 0) or 0
        if price > 0:
            price_map[symbol] = price
            statuses[symbol] = str((ticker or {}).get("price_status") or "live")
            success += 1
        else:
            old = next((row for row in rows if str(row.get("symbol") or "").upper() == symbol), {})
            fallback = safe_number(old.get("current_price"), 0) or 0
            price_map[symbol] = fallback
            statuses[symbol] = "stale" if fallback > 0 else "missing"
            missing += 1
    if symbols:
        append_debug_log(POSITION_PRICE_LOG, f"position_page_update symbols={len(symbols)} success={success} missing={missing}")
        try:
            update_simulation(price_map, [], statuses)
            summary = get_sim_account_summary()
        except Exception as exc:
            append_debug_log(POSITION_PRICE_LOG, f"position_page_update_simulation_failed error={repr(exc)}")
            st.warning(f"持仓价格刷新失败：{exc!r}")
    return summary


def render_sim_overview_window(summary: dict[str, Any]) -> None:
    """总览页模拟交易精简窗口。"""
    account = summary.get("account") or {}
    positions = [p for p in summary.get("positions", []) if p.get("status") in {"open", "partially_closed"}]
    orders = [o for o in summary.get("orders", []) if o.get("status") == "pending"]
    history = summary.get("history") or []
    last_trade = history[0] if history else {}
    pnl = float(account.get("total_pnl", 0) or 0)
    render_html(
        f"""
        <div class="app-shell">
          <div class="module-card">
            <div class="module-title">模拟交易概览</div>
            <div class="module-desc">当前为模拟交易，不会使用真实资金，不会执行真实订单。</div>
            <div class="committee-grid">
              <div class="summary-card"><div class="summary-label">模拟账户权益</div><div class="summary-value">{format_compact(float(account.get("equity", 0) or 0))} USDT</div></div>
              <div class="summary-card"><div class="summary-label">今日盈亏</div><div class="summary-value {_signal_color("支持交易" if float(account.get("daily_pnl", 0) or 0) >= 0 else "反对交易")}">{float(account.get("daily_pnl", 0) or 0):+.2f} USDT</div></div>
              <div class="summary-card"><div class="summary-label">当前持仓</div><div class="summary-value blue">{len(positions)}</div></div>
              <div class="summary-card"><div class="summary-label">待触发订单</div><div class="summary-value yellow">{len(orders)}</div></div>
              <div class="summary-card"><div class="summary-label">累计盈亏</div><div class="summary-value {_signal_color("支持交易" if pnl >= 0 else "反对交易")}">{pnl:+.2f} USDT</div></div>
              <div class="summary-card"><div class="summary-label">模拟状态</div><div class="summary-value yellow">{escape(str(account.get("status", "stopped")))}</div></div>
            </div>
            <div class="status-card" style="margin-top:8px;">最近一笔交易：{escape(str(last_trade.get("symbol", "暂无")))}｜{escape(str(last_trade.get("close_reason", "暂无历史")))}｜盈亏 {float(last_trade.get("pnl", 0) or 0):+.2f} USDT</div>
            <a class="watch-pill" href="?page=trading" target="_self" style="margin-top:8px;">进入模拟交易中心</a>
          </div>
        </div>
        """
    )


def render_live_safety_overview_window(status: dict[str, Any]) -> None:
    """总览页实盘安全精简窗口。"""
    settings = status.get("settings") or {}
    connection = status.get("connection") or {}
    permission = status.get("permission") or {}
    withdraw = status.get("withdraw") or {}
    recent = (status.get("recent_audit") or [{}])[0] if status.get("recent_audit") else {}
    st.markdown(
        f"""
        <div class="app-shell">
          <div class="module-card">
            <div class="module-title">实盘安全中心</div>
            <div class="module-desc">默认不会执行真实订单，当前仅用于安全检查、只读监控和订单预览。</div>
            <div class="committee-grid">
              <div class="summary-card"><div class="summary-label">当前模式</div><div class="summary-value yellow">{escape(str(settings.get("mode", "read_only")))}</div></div>
              <div class="summary-card"><div class="summary-label">API状态</div><div class="summary-value {_signal_color("支持交易" if connection.get("ok") else "反对交易")}">{escape(str(connection.get("status", "未检查")))}</div></div>
              <div class="summary-card"><div class="summary-label">权限状态</div><div class="summary-value">{escape(str(permission.get("permission_status", "未配置")))}</div></div>
              <div class="summary-card"><div class="summary-label">提现权限</div><div class="summary-value {_signal_color("反对交易" if withdraw.get("status") == "高危开启" else "支持交易")}">{escape(str(withdraw.get("status", "未知")))}</div></div>
              <div class="summary-card"><div class="summary-label">安全锁</div><div class="summary-value {_signal_color("反对交易" if settings.get("kill_switch_enabled") else "支持交易")}">{'已触发' if settings.get("kill_switch_enabled") else '未触发'}</div></div>
              <div class="summary-card"><div class="summary-label">实盘候选</div><div class="summary-value {_signal_color("支持交易" if status.get("allow_live_candidate") else "反对交易")}">{'允许审查' if status.get("allow_live_candidate") else '暂不允许'}</div></div>
            </div>
            <div class="status-card" style="margin-top:8px;">最近安全事件：{escape(str(recent.get("event", "暂无")))}｜{escape(str(recent.get("result", "")))}｜{escape(str(recent.get("reason", "")))}</div>
            <a class="watch-pill" href="?page=live" target="_self" style="margin-top:8px;">进入实盘安全中心</a>
          </div>
        </div>
        """,
        unsafe_allow_html=True,
    )


def render_server_overview_window() -> None:
    """总览页服务器长期运行状态卡。"""
    health = get_server_health()
    backup = health.get("backup") or {}
    metrics = health.get("metrics") or {}
    st.markdown(
        f"""
        <div class="app-shell">
          <div class="module-card">
            <div class="module-title">服务器运行状态</div>
            <div class="committee-grid">
              <div class="summary-card"><div class="summary-label">状态</div><div class="summary-value {_signal_color(str(health.get("status", "")))}">{escape(str(health.get("status", "运行中")))}</div></div>
              <div class="summary-card"><div class="summary-label">模式</div><div class="summary-value yellow">{escape(str(health.get("mode", "READ_ONLY")))}</div></div>
              <div class="summary-card"><div class="summary-label">运行时长</div><div class="summary-value blue">{escape(str(health.get("uptime", "-")))}</div></div>
              <div class="summary-card"><div class="summary-label">心跳</div><div class="summary-value green">{escape(str(health.get("last_heartbeat_time", "-")))}</div></div>
              <div class="summary-card"><div class="summary-label">日志</div><div class="summary-value {'green' if health.get("logs_writable") else 'red'}">{"正常" if health.get("logs_writable") else "不可写"}</div></div>
              <div class="summary-card"><div class="summary-label">数据目录</div><div class="summary-value {'green' if health.get("data_writable") else 'red'}">{"正常" if health.get("data_writable") else "不可写"}</div></div>
              <div class="summary-card"><div class="summary-label">磁盘剩余</div><div class="summary-value yellow">{float(metrics.get("disk_free_gb", 0) or 0):.1f} GB</div></div>
              <div class="summary-card"><div class="summary-label">最近备份</div><div class="summary-value {'green' if backup.get("latest_ok") else 'yellow'}">{escape(str(backup.get("latest_backup_time") or "未备份"))}</div></div>
            </div>
            <div class="module-desc" style="margin-top:8px;">服务器重启默认进入 READ_ONLY，真实交易和自动实盘不会自动恢复。</div>
            <a class="watch-pill" href="?page=server" target="_self" style="margin-top:8px;">查看服务器健康</a>
          </div>
        </div>
        """,
        unsafe_allow_html=True,
    )


def render_notification_overview_window() -> None:
    """总览页通知摘要。"""
    summary = get_notification_summary()
    latest = summary.get("latest") or []
    latest_text = "暂无未读通知"
    if latest:
        top = latest[0]
        latest_text = f"{top.get('priority')}｜{top.get('title')}｜{top.get('message', '')[:80]}"
    st.markdown(
        f"""
        <div class="app-shell">
          <div class="module-card">
            <div class="module-title">通知中心</div>
            <div class="committee-grid">
              <div class="summary-card"><div class="summary-label">未读通知</div><div class="summary-value {'red' if summary.get("unread_count") else 'green'}">{summary.get("unread_count", 0)}</div></div>
              <div class="summary-card"><div class="summary-label">紧急通知</div><div class="summary-value {'red' if summary.get("urgent_count") else 'green'}">{summary.get("urgent_count", 0)}</div></div>
              <div class="summary-card"><div class="summary-label">待审批提醒</div><div class="summary-value yellow">{summary.get("approval_count", 0)}</div></div>
              <div class="summary-card"><div class="summary-label">风险提醒</div><div class="summary-value red">{summary.get("risk_count", 0)}</div></div>
              <div class="summary-card"><div class="summary-label">服务器提醒</div><div class="summary-value yellow">{summary.get("server_count", 0)}</div></div>
              <div class="summary-card"><div class="summary-label">实盘提醒</div><div class="summary-value yellow">{summary.get("live_count", 0)}</div></div>
            </div>
            <div class="status-card" style="margin-top:8px;">最近提醒：{escape(str(latest_text))}</div>
            <a class="watch-pill" href="?page=remote" target="_self" style="margin-top:8px;">查看通知中心</a>
          </div>
        </div>
        """,
        unsafe_allow_html=True,
    )


def render_strategy_committee_preview(strategy: dict[str, Any]) -> None:
    """首页策略委员看板。"""
    direction = _direction_text(str(strategy.get("direction", "neutral")))
    decision = str(strategy.get("local_vote_decision", "只观察"))
    grade = str(strategy.get("local_vote_grade", "D"))
    st.markdown(
        f"""
        <div class="app-shell">
            <div class="module-card">
              <div class="module-title">策略委员看板</div>
            <div class="module-desc">本地策略是基础提案层，DeepSeek/Gemini 当前为正式投票委员；观察池委员和策略验证委员暂为影子复核。</div>
            <div class="module-grid">
              <div class="status-card">
                <b>本地策略委员</b><br>
                投票分：<span class="{_signal_color(decision)}">{strategy.get("local_vote_score", 0)} / 100 · {escape(grade)}级</span><br>
                投票决议：<span class="{_signal_color(decision)}">{escape(decision)}</span><br>
                方向：{escape(direction)}｜建议：{escape(str(strategy.get("action", "观望")))}<br>
                策略：{escape(str(strategy.get("strategy_name", "无有效策略")))}｜仓位：{escape(str(strategy.get("position_suggestion", "0%")))}<br>
                理由：{escape(str(strategy.get("local_vote_reason", "等待策略数据同步")))}
              </div>
              <div class="status-card"><b>DeepSeek委员</b><br>正式投票<br>只读取脱敏摘要，不下单、不绕过风控。</div>
              <div class="status-card"><b>Gemini委员</b><br>正式投票<br>参与权重表决，不读取密钥、不硬否决。</div>
              <div class="status-card"><b>人工仓位干预</b><br>受限启用<br>只能低于风控最大仓位，超系统建议需确认。</div>
            </div>
          </div>
        </div>
        """,
        unsafe_allow_html=True,
    )


def render_watchlist_quick_controls(symbol: str, key_prefix: str, source: str = "manual") -> None:
    """当前交易对象的观察池快捷操作。"""
    normalized = str(symbol or "").upper().strip()
    if not normalized:
        return
    c1, c2 = st.columns(2)
    watched = is_watched(normalized)
    with c1:
        if watched:
            st.button(f"{normalized} 已在观察池", key=f"{key_prefix}_watch_exists", disabled=True, use_container_width=True)
        elif st.button(f"加入观察池：{normalized}", key=f"{key_prefix}_watch_add", use_container_width=True):
            add_to_watchlist(normalized, source=source)
            st.success(f"{normalized} 已加入观察池")
    with c2:
        if watched and st.button(f"移出观察池：{normalized}", key=f"{key_prefix}_watch_remove", use_container_width=True):
            remove_from_watchlist(normalized)
            st.warning(f"{normalized} 已移出观察池")


def render_home(ticker: dict[str, Any] | None, snapshot: dict[str, Any], scores: dict[str, Any], symbols: list[str], rankings: dict[str, list[dict[str, Any]]] | None = None) -> None:
    """总览页。"""
    render_page_head("home")
    render_symbol_search_panel(symbols, "home")
    render_watchlist_quick_controls(st.session_state.current_symbol, "home", source="manual")
    change_class = "green" if ticker and ticker["price_change_percent"] >= 0 else "red"
    ai_text, ai_color = build_ai_status(scores)
    render_metric_grid(
        [
            ("当前交易对象", snapshot.get("current_symbol", "-"), ""),
            ("当前价格", format_price(ticker["last_price"]) if ticker else "正在获取", ""),
            ("24小时涨跌幅", format_percent(ticker["price_change_percent"]) if ticker else "正在获取", change_class),
            ("市场状态", scores["trend_label"], "yellow"),
            ("趋势评分", score_text(scores.get("trend_score")), score_color(scores.get("trend_score"), 65)),
            ("风险评分", score_text(scores.get("risk_score")), "yellow" if scores.get("risk_score") is None or scores.get("risk_score", 0) < 65 else "red"),
            ("机会评分", score_text(scores.get("opportunity_score")), score_color(scores.get("opportunity_score"), 70)),
            ("AI建议", ai_text, ai_color),
        ]
    )
    if ticker:
        render_metric_grid(
            [
                ("24h最高价", format_price(ticker["high_price"]), ""),
                ("24h最低价", format_price(ticker["low_price"]), ""),
                ("24h成交额", format_compact(ticker["quote_volume"]), ""),
                ("更新时间", snapshot.get("last_update_time", "初始化中"), ""),
            ]
    )
    render_trade_opportunity_board_realtime(rankings, compact=True)
    committee_symbol = str(st.session_state.get("committee_active_symbol") or st.session_state.current_symbol)
    render_committee_overview_window(build_current_committee_decision(committee_symbol, market_cache.get_ticker(committee_symbol)))
    sim_summary = update_simulation(build_sim_price_map(st.session_state.current_symbol), [])
    render_sim_overview_window(sim_summary)
    render_live_safety_overview_window(get_live_safety_status())
    render_server_overview_window()
    render_notification_overview_window()
    render_strategy_committee_preview(build_current_local_strategy(st.session_state.current_symbol, ticker))
    current = escape(str(st.session_state.get("current_symbol", "BTCUSDT")))
    st.markdown(
        f"""<div class="app-shell"><div class="module-card"><div class="module-title">快捷操作</div>
        <div class="quick-grid">
          <a class="quick-button rank-link" href="?page=positions&symbol={current}" target="_self">查看持仓与订单</a>
          <a class="quick-button rank-link" href="?page=market&symbol={current}" target="_self">查看机会榜</a>
          <a class="quick-button rank-link" href="?page=trade&symbol={current}" target="_self">模拟/实盘交易</a>
          <a class="quick-button rank-link" href="?page=server&symbol={current}" target="_self">运行与安全状态</a>
        </div></div></div>""",
        unsafe_allow_html=True,
    )


def watch_action_html(symbol: str, page: str, source: str) -> str:
    """生成榜单内联观察池操作。"""
    if is_watched(symbol):
        return '<span class="watch-pill done">已观察</span>'
    return f'<a class="watch-pill" href="?page={page}&symbol={symbol}&watch_add={symbol}" target="_self">加入观察池</a>'


def render_rank_list(title: str, rows: list[dict[str, Any]], mode: str) -> None:
    """渲染专业市场榜单列表。"""
    st.markdown(f'<div class="list-card"><div class="module-title">{title}</div>', unsafe_allow_html=True)
    st.markdown('<div class="opp-row compact-five rank-head"><div>交易对象</div><div>价格</div><div>观察</div><div>涨跌</div><div>成交额</div></div>', unsafe_allow_html=True)
    if not rows:
        st.markdown('<div class="pending">正在获取行情</div>', unsafe_allow_html=True)
    active_page = st.session_state.active_page
    for index, row in enumerate(rows[:10], start=1):
        medal_class = "gold" if index == 1 else "silver" if index == 2 else "bronze" if index == 3 else ""
        change_class = "green" if row["price_change_percent"] >= 0 else "red"
        symbol = row["symbol"]
        href = kline_href(symbol)
        st.markdown(
            f"""
            <div class="opp-row compact-five">
              <div>
                <a class="rank-link" href="{href}" target="_self"><div class="opp-symbol"><span class="rank-index {medal_class}">#{index}</span> {symbol}</div></a>
                <div class="opp-meta">点击查看K线图</div>
              </div>
              <div class="opp-symbol">{format_price(row["last_price"])}</div>
              <div>{watch_action_html(symbol, active_page, title)}</div>
              <div class="{change_class}" style="font-weight:900;">{format_percent(row["price_change_percent"])}</div>
              <div class="rank-volume">{format_compact(row["quote_volume"])}</div>
            </div>""",
            unsafe_allow_html=True,
        )
    st.markdown("</div>", unsafe_allow_html=True)


def _render_score_sources(row: dict[str, Any]) -> str:
    risk = row.get("risk_breakdown") or {}
    opportunity = row.get("opportunity_breakdown") or {}
    risk_sources = risk.get("main_risk_sources") or []
    opportunity_sources = opportunity.get("main_opportunity_sources") or []
    risk_text = "；".join(escape(str(item)) for item in risk_sources[:3]) if risk_sources else "暂无主要风险来源。"
    opportunity_text = "；".join(escape(str(item)) for item in opportunity_sources[:4]) if opportunity_sources else "暂无机会来源拆解。"
    risk_rows = "".join(
        f"<div class=\"opp-meta\">{label}：{int(float(risk.get(key, 0) or 0))}</div>"
        for key, label in [
            ("volatility_risk", "波动"),
            ("overheat_risk", "过热"),
            ("funding_risk", "Funding"),
            ("crowding_risk", "拥挤"),
            ("liquidation_risk", "清算"),
            ("orderflow_risk", "盘口大单"),
            ("data_quality_risk", "数据质量"),
            ("combo_risk_boost", "组合放大"),
        ]
    )
    opp_rows = "".join(
        f"<div class=\"opp-meta\">{label}：{int(float(opportunity.get(key, 0) or 0))}</div>"
        for key, label in [
            ("trend_opportunity", "趋势"),
            ("capital_opportunity", "资金"),
            ("structure_opportunity", "结构"),
            ("orderflow_opportunity", "盘口大单"),
            ("liquidity_opportunity", "流动性"),
            ("tradeability_opportunity", "可交易性"),
        ]
    )
    diagnostic = str(row.get("risk_model_diagnostic") or "normal")
    diagnostic_text = {
        "too_sensitive": "风险模型可能过度敏感，请检查阈值设置。",
        "too_loose": "风险模型可能过于宽松，请检查阈值设置。",
        "insufficient_data": "样本或字段不足，当前统计仅供观察。",
    }.get(diagnostic, "风险模型状态正常。")
    return f"""
      <details class="opp-meta" style="margin-top:6px;">
        <summary>评分拆解 / 风险来源</summary>
        <div class="committee-grid" style="margin-top:6px;">
          <div class="status-card"><b>机会来源</b><br>{opportunity_text}<br>{opp_rows}</div>
          <div class="status-card"><b>风险来源</b><br>{risk_text}<br>{risk_rows}</div>
        </div>
        <div class="opp-meta">诊断：{escape(diagnostic_text)}</div>
      </details>
    """


def render_opportunity_list(title: str, rows: list[dict[str, Any]], mode: str) -> None:
    """渲染机会榜专业列表。"""
    active_page = st.session_state.active_page
    fast_status = get_fast_opportunity_status()
    fast_target = str(fast_status.get("current_target") or "")
    st.markdown(f'<div class="list-card"><div class="module-title">{title}</div>', unsafe_allow_html=True)
    if not rows:
        st.markdown('<div class="pending">市场机会榜暂不可用，请稍后重试</div>', unsafe_allow_html=True)
        st.markdown("</div>", unsafe_allow_html=True)
        return
    st.markdown(
        '<div class="opp-row compact-five rank-head"><div>交易对象</div><div>价格</div><div>观察</div><div>涨跌</div><div>机会/风险</div></div>',
        unsafe_allow_html=True,
    )
    for index, row in enumerate(rows[:10], start=1):
        symbol = row.get("symbol", "")
        opportunity_score = safe_score(row.get("final_opportunity_score", row.get("opportunity_score")))
        raw_score = safe_score(row.get("raw_opportunity_score"), opportunity_score)
        risk_score = safe_score(row.get("risk_score"))
        risk_penalty = safe_score(row.get("risk_penalty"), 0)
        score_cap = safe_score(row.get("score_cap"), 100)
        status = str(row.get("opportunity_status", row.get("advice", "观察")))
        fast_badges = []
        if index == 1:
            fast_badges.append("TOP1")
        if str(symbol).upper() == fast_target:
            fast_badges.append("快速预判目标")
        if safe_compare_gte(opportunity_score, 80):
            fast_badges.append("80分候选")
        fast_text = " · ".join(fast_badges) if fast_badges else "等待触发"
        change = safe_number(row.get("price_change_percent"))
        change_class = "green" if (change is not None and change >= 0) else "red" if change is not None else "yellow"
        href = kline_href(symbol)
        st.markdown(
            f"""
            <div class="opp-row compact-five">
              <div>
                <a class="rank-link" href="{href}" target="_self"><div class="opp-symbol">#{index} {symbol}</div></a>
                <div class="opp-meta">{row.get("current_market_state", "观察")} · {row.get("advice", "观察等待")}</div>
                <div class="opp-meta">{escape(fast_text)}</div>
              </div>
              <div class="opp-symbol">{format_price(row.get("last_price", 0))}</div>
              <div>{watch_action_html(symbol, active_page, title)}</div>
              <div class="{change_class}" style="font-weight:900;">{format_percent(change)}</div>
              <div>
                <div class="score-pill">终{format_score(opportunity_score)} / 风{format_score(risk_score)}</div>
                <div class="opp-meta">原始{format_score(raw_score)}｜扣{format_score(risk_penalty)}｜封顶{format_score(score_cap)}</div>
                <div class="opp-meta">{escape(status)}｜额 {format_compact(row.get("quote_volume", 0))}</div>
              </div>
            </div>""",
            unsafe_allow_html=True,
        )
        st.markdown(_render_score_sources(row), unsafe_allow_html=True)
    st.markdown("</div>", unsafe_allow_html=True)


def render_fast_opportunity_panel() -> None:
    """显示 TOP1 三秒快速捕捉与委员会快速预判状态。"""
    status = get_fast_opportunity_status()
    capture = status.get("latest_capture") or {}
    precheck = status.get("latest_precheck") or {}
    candidate = status.get("latest_candidate") or {}
    settings = status.get("settings") or {}
    symbol = capture.get("symbol") or status.get("current_target") or "-"
    capture_state = "通过" if capture.get("trigger_committee_precheck") else "观察"
    precheck_state = precheck.get("fast_action") or "等待"
    candidate_state = candidate.get("message") or "暂无候选"
    st.markdown(
        f"""
        <div class="module-card">
          <div class="module-title">TOP1 三秒快速捕捉</div>
          <div class="metric-grid">
            <div class="metric-box"><div class="metric-label">当前目标</div><div class="metric-value blue">{escape(str(symbol))}</div></div>
            <div class="metric-box"><div class="metric-label">快速评分</div><div class="metric-value green">{int(capture.get("fast_score", status.get("target_score", 0)) or 0)}</div></div>
            <div class="metric-box"><div class="metric-label">快速捕捉</div><div class="metric-value {'green' if capture_state == '通过' else 'yellow'}">{capture_state}</div></div>
            <div class="metric-box"><div class="metric-label">委员会预判</div><div class="metric-value {'green' if precheck_state == '进入候选' else 'yellow'}">{escape(str(precheck_state))}</div></div>
            <div class="metric-box"><div class="metric-label">榜单刷新</div><div class="metric-value yellow">{int(settings.get("TOP10_OPPORTUNITY_REFRESH_SECONDS", 10))}秒</div></div>
            <div class="metric-box"><div class="metric-label">触发阈值</div><div class="metric-value blue">{int(settings.get("OPPORTUNITY_TRIGGER_SCORE", 80))}分</div></div>
          </div>
          <div class="module-desc" style="margin-top:8px;">
            {escape(str(candidate_state))}<br>
            机会评分达到80分仅代表进入交易候选，真实交易仍需完整委员会、风控、实盘安全和人工确认。DeepSeek/Gemini 不参与高频快速调用。
          </div>
        </div>
        """,
        unsafe_allow_html=True,
    )
    with st.expander("机会发现设置", expanded=False):
        s1, s2 = st.columns(2)
        new_settings = dict(settings)
        new_settings["ENABLE_FAST_OPPORTUNITY_CAPTURE"] = s1.checkbox("启用TOP1快速捕捉", value=bool(settings.get("ENABLE_FAST_OPPORTUNITY_CAPTURE", True)))
        new_settings["ENABLE_FAST_COMMITTEE_PRECHECK"] = s2.checkbox("启用委员会快速预判", value=bool(settings.get("ENABLE_FAST_COMMITTEE_PRECHECK", True)))
        new_settings["ENABLE_COMMITTEE_ANCHOR_TOP1"] = st.checkbox("委员会当前交易对象自动锚定TOP1", value=bool(settings.get("ENABLE_COMMITTEE_ANCHOR_TOP1", True)))
        c1, c2, c3 = st.columns(3)
        new_settings["TOP10_OPPORTUNITY_REFRESH_SECONDS"] = c1.number_input("TOP10刷新秒数", min_value=10, max_value=60, value=int(settings.get("TOP10_OPPORTUNITY_REFRESH_SECONDS", 10)), step=5)
        new_settings["TOP1_FAST_CAPTURE_SECONDS"] = c2.number_input("TOP1捕捉秒数", min_value=3, max_value=30, value=int(settings.get("TOP1_FAST_CAPTURE_SECONDS", 3)), step=1)
        new_settings["COMMITTEE_FAST_PRECHECK_SECONDS"] = c3.number_input("快速预判秒数", min_value=30, max_value=120, value=int(settings.get("COMMITTEE_FAST_PRECHECK_SECONDS", 30)), step=10)
        c4, c5, c6 = st.columns(3)
        new_settings["COMMITTEE_FULL_REVIEW_SECONDS"] = c4.number_input("完整复核秒数", min_value=10, max_value=120, value=int(settings.get("COMMITTEE_FULL_REVIEW_SECONDS", 15)), step=5)
        new_settings["OPPORTUNITY_TRIGGER_SCORE"] = c5.number_input("机会触发分数", min_value=60, max_value=95, value=int(settings.get("OPPORTUNITY_TRIGGER_SCORE", 80)), step=1)
        new_settings["OPPORTUNITY_DUPLICATE_COOLDOWN_SECONDS"] = c6.number_input("重复候选冷却秒数", min_value=60, max_value=1800, value=int(settings.get("OPPORTUNITY_DUPLICATE_COOLDOWN_SECONDS", 120)), step=30)
        c7, c8, c9 = st.columns(3)
        new_settings["COMMITTEE_TARGET_MIN_STABLE_CYCLES"] = c7.number_input("TOP1稳定确认次数", min_value=1, max_value=5, value=int(settings.get("COMMITTEE_TARGET_MIN_STABLE_CYCLES", 2)), step=1)
        new_settings["COMMITTEE_TARGET_SWITCH_SCORE_GAP"] = c8.number_input("切换分差", min_value=1, max_value=20, value=int(settings.get("COMMITTEE_TARGET_SWITCH_SCORE_GAP", 5)), step=1)
        new_settings["COMMITTEE_TARGET_COOLDOWN_SECONDS"] = c9.number_input("切换冷却秒数", min_value=10, max_value=300, value=int(settings.get("COMMITTEE_TARGET_COOLDOWN_SECONDS", 30)), step=5)
        c10, c11, c12 = st.columns(3)
        new_settings["COMMITTEE_REVIEW_TOP_N"] = c10.number_input("快速候选评审TOP N", min_value=1, max_value=10, value=int(settings.get("COMMITTEE_REVIEW_TOP_N", 5)), step=1)
        new_settings["COMMITTEE_LIGHT_TRACK_TOP_N"] = c11.number_input("轻量跟踪TOP N", min_value=5, max_value=20, value=int(settings.get("COMMITTEE_LIGHT_TRACK_TOP_N", 10)), step=1)
        new_settings["TOP2_TO_TOP5_PRECHECK_SECONDS"] = c12.number_input("TOP2-TOP5预判秒数", min_value=3, max_value=30, value=int(settings.get("TOP2_TO_TOP5_PRECHECK_SECONDS", 5)), step=1)
        c13, c14 = st.columns(2)
        new_settings["FULL_REVIEW_TOP_N"] = c13.number_input("完整复核TOP N", min_value=1, max_value=10, value=int(settings.get("FULL_REVIEW_TOP_N", 10)), step=1)
        new_settings["FULL_REVIEW_INTERVAL_SECONDS"] = c14.number_input("完整复核间隔秒数", min_value=10, max_value=120, value=int(settings.get("FULL_REVIEW_INTERVAL_SECONDS", 15)), step=5)
        if st.button("保存机会发现设置", use_container_width=True):
            save_fast_opportunity_settings(new_settings)
            st.success("机会发现设置已保存，后台刷新将在下一轮读取。")


def _combined_trade_opportunities(rankings: dict[str, list[dict[str, Any]]] | None, limit: int = 10) -> list[dict[str, Any]]:
    rankings = rankings or {}
    by_symbol: dict[str, dict[str, Any]] = {}
    for key, label in [
        ("long_opportunities", "多头机会榜"),
        ("short_opportunities", "空头机会榜"),
        ("strong", "强势币榜"),
        ("abnormal", "异动币榜"),
    ]:
        for row in rankings.get(key, []) or []:
            symbol = str(row.get("symbol", "")).upper()
            if not symbol:
                continue
            item = dict(row)
            item["opportunity_source"] = label
            old = by_symbol.get(symbol)
            if old is None or safe_score(item.get("final_opportunity_score", item.get("opportunity_score")), -1) > safe_score(old.get("final_opportunity_score", old.get("opportunity_score")), -1):
                by_symbol[symbol] = item
    for item in get_watchlist_candidates_for_committee():
        symbol = str(item.get("symbol", "")).upper()
        if not symbol:
            continue
        watch_score = safe_score(item.get("watch_score"), 0) or 0
        confidence = safe_score(item.get("confidence"), watch_score) or watch_score
        risk = safe_score(item.get("risk_score"), 50)
        opportunity_score = max(watch_score, min(100, confidence))
        if opportunity_score < 61 or safe_compare_gte(risk, 80):
            continue
        ticker = market_cache.get_ticker(symbol) or {}
        row = {
            "symbol": symbol,
            "last_price": ticker.get("last_price"),
            "current_price": ticker.get("last_price"),
            "price_change_percent": ticker.get("price_change_percent"),
            "quote_volume": ticker.get("quote_volume", 0),
            "final_opportunity_score": opportunity_score,
            "raw_opportunity_score": opportunity_score,
            "opportunity_score": opportunity_score,
            "risk_score": risk,
            "risk_penalty": 0,
            "score_cap": 100,
            "direction": item.get("local_strategy_action") or "观察",
            "advice": item.get("local_strategy_action") or "观察池候选",
            "opportunity_status": item.get("status") or "观察池候选",
            "current_market_state": item.get("strategy_name") or "观察池重点跟踪",
            "opportunity_source": "观察池候选",
            "watch_score": watch_score,
            "watchlist_candidate": True,
            "watchlist_reason": item.get("main_reason"),
        }
        old = by_symbol.get(symbol)
        if old is None or safe_score(row.get("final_opportunity_score"), -1) > safe_score(old.get("final_opportunity_score", old.get("opportunity_score")), -1):
            by_symbol[symbol] = row
    return sorted(by_symbol.values(), key=lambda row: safe_score(row.get("final_opportunity_score", row.get("opportunity_score")), -1), reverse=True)[:limit]


def _top10_precheck_map(rankings: dict[str, list[dict[str, Any]]] | None) -> dict[str, dict[str, Any]]:
    try:
        results = run_committee_top10_precheck(rankings, 10)
    except Exception:
        results = list((get_fast_opportunity_status().get("latest_top10_precheck") or []))
    return {str(item.get("symbol", "")).upper(): item for item in results}


def render_top10_committee_precheck_summary(precheck_by_symbol: dict[str, dict[str, Any]]) -> None:
    if not precheck_by_symbol:
        return
    results = list(precheck_by_symbol.values())
    allowed = [item for item in results if item.get("allowed_candidate")]
    blocked = [item for item in results if not item.get("allowed_candidate")]
    watch = [item for item in blocked if str(item.get("fast_action")) == "观察复核"]
    top_allowed = "、".join(str(item.get("symbol")) for item in allowed[:5]) or "暂无"
    main_blocks = []
    for item in blocked[:4]:
        reason = "；".join(str(r) for r in list(item.get("block_reasons") or [])[:2]) or "等待完整复核"
        main_blocks.append(f"{item.get('symbol')}：{reason}")
    block_text = "｜".join(main_blocks) if main_blocks else "暂无明显阻断。"
    st.markdown(
        f"""
        <div class="status-card" style="margin-top:8px;">
          <b>机会榜TOP10委员会快速判断</b><br>
          可进候选：{len(allowed)} 个｜观察复核：{len(watch)} 个｜阻断/等待：{len(blocked)} 个<br>
          候选对象：{escape(top_allowed)}<br>
          主要阻断：{escape(block_text)}<br>
          该判断为轻量预判，不调用DeepSeek/Gemini，不替代完整委员会复核。
        </div>
        """,
        unsafe_allow_html=True,
    )
    multi_review = list((get_fast_opportunity_status().get("latest_multi_review") or []))
    if multi_review:
        rows_html = ""
        for item in multi_review[:10]:
            status = str(item.get("review_status", "pending"))
            symbol = str(item.get("symbol", "-"))
            created = "已生成候选" if item.get("candidate_created") else "未生成候选"
            reason = str(item.get("block_reason") or item.get("candidate_id") or "等待下一轮复核")
            review_count = int(safe_score(item.get("review_count"), 0) or 0)
            reject_count = int(safe_score(item.get("reject_count"), 0) or 0)
            opportunity_round = int(safe_score(item.get("opportunity_round"), 1) or 1)
            rows_html += (
                f'<div class="opp-meta">#{escape(str(item.get("rank", "-")))} {escape(symbol)}｜'
                f'第{opportunity_round}轮｜审查{review_count}次｜否决{reject_count}次｜'
                f'{escape(status)}｜{escape(created)}｜{escape(reason)}</div>'
            )
        st.markdown(
            f"""
            <div class="status-card" style="margin-top:8px;">
              <b>多机会并行评审队列</b><br>
              {rows_html}
            </div>
            """,
            unsafe_allow_html=True,
        )


def _multi_review_map() -> dict[str, dict[str, Any]]:
    return {str(item.get("symbol", "")).upper(): item for item in (get_fast_opportunity_status().get("latest_multi_review") or [])}


def _opportunity_committee_summary(symbol: str, row: dict[str, Any], precheck: dict[str, Any], multi: dict[str, Any]) -> dict[str, Any]:
    """把快速预判/多机会评审合成为机会榜可直接展示的委员会摘要。"""
    allowed = bool(precheck.get("allowed_candidate"))
    review_status = str(multi.get("review_status") or precheck.get("review_status") or "pending")
    candidate_created = bool(multi.get("candidate_created"))
    block_reason = str(multi.get("block_reason") or "；".join(precheck.get("block_reasons") or []) or "")
    if candidate_created:
        candidate_status = "自动候选"
    elif review_status == "blocked":
        candidate_status = "阻止"
    elif allowed:
        candidate_status = "候选池"
    elif review_status in {"watching", "fast_checked"}:
        candidate_status = "观察池"
    else:
        candidate_status = "未进入"

    action = str(precheck.get("fast_action") or ("轻仓试单" if allowed else "等待复核"))
    decision = "支持做多" if allowed and precheck.get("direction") == "long" else "支持做空" if allowed and precheck.get("direction") == "short" else "建议观察" if review_status != "blocked" else "禁止开仓"
    review_count = int(safe_score(multi.get("review_count", precheck.get("review_count")), 0) or 0)
    reject_count = int(safe_score(multi.get("reject_count", row.get("reject_count")), 0) or 0)
    opportunity_round = int(safe_score(multi.get("opportunity_round", row.get("opportunity_round")), 1) or 1)
    lifecycle_status = str(multi.get("status") or row.get("status") or "")
    removed_reason = str(multi.get("removed_reason") or row.get("removed_reason") or "")
    cooldown = {"has_cooldown": False, "cooldown_type": "", "remaining_seconds": 0, "reason": ""}
    cooldown_until = safe_score(multi.get("cooldown_until", row.get("cooldown_until")), 0) or 0
    remaining = max(0, int(cooldown_until - time.time())) if cooldown_until else 0
    if "冷却" in block_reason or remaining > 0:
        mm, ss = divmod(remaining, 60)
        reason = f"第{opportunity_round}轮｜否决{reject_count}次｜冷却中 {mm:02d}:{ss:02d}｜审查{review_count}次"
        if remaining <= 0:
            reason = block_reason if "审查" in block_reason else f"{block_reason} 审查 {review_count} 次。"
        cooldown = {"has_cooldown": True, "cooldown_type": "candidate_or_reject_cooldown", "remaining_seconds": remaining, "reason": reason}
    return {
        "review_status": review_status,
        "committee_decision": decision,
        "final_action": action,
        "trade_permission": "candidate" if allowed else "blocked" if review_status == "blocked" else "observe_only",
        "resonance_level": "等待完整复核" if review_status in {"pending", "fast_checked"} else "被否决" if review_status == "blocked" else "中等共振",
        "support_weight": 0,
        "observe_weight": 0,
        "oppose_weight": 0,
        "hard_veto": review_status == "blocked" and safe_compare_gte(precheck.get("risk_score"), 85),
        "soft_warning_count": len(precheck.get("warnings") or []),
        "review_count": review_count,
        "reject_count": reject_count,
        "opportunity_round": opportunity_round,
        "lifecycle_status": lifecycle_status,
        "removed_reason": removed_reason,
        "deepseek_status": "外部AI待补充",
        "gemini_status": "外部AI待补充",
        "cooldown_status": cooldown,
        "candidate_status": candidate_status,
        "next_action": "等待自动交易检查" if candidate_created else "进入候选检查" if allowed else block_reason or "继续观察",
        "last_review_time": str(multi.get("last_review_time") or precheck.get("timestamp") or ""),
        "review_age_seconds": 0,
    }


def render_trade_opportunity_board(rankings: dict[str, list[dict[str, Any]]] | None, compact: bool = False) -> None:
    """实时交易机会榜单：明确展示TOP1并说明委员会锚定状态。"""
    rankings = market_cache.get_rankings() or rankings or {}
    rows = _combined_trade_opportunities(rankings, 10)
    precheck_by_symbol = _top10_precheck_map(rankings)
    multi_by_symbol = _multi_review_map()
    fast_status = get_fast_opportunity_status()
    fast_target = str(fast_status.get("current_target") or "").upper()
    committee_symbol = str(st.session_state.get("committee_active_symbol") or st.session_state.get("current_symbol", "")).upper()
    queue_symbol = str(st.session_state.get("committee_review_queue_symbol") or "").upper()
    settings = fast_status.get("settings") or {}
    trigger_score = int(settings.get("OPPORTUNITY_TRIGGER_SCORE", 80) or 80)
    st.markdown(
        f"""
        <div class="app-shell">
          <div class="module-card">
            <div class="module-title">实时交易机会榜单</div>
            <div class="module-desc">交易机会榜是系统所有交易机会的总入口。每个币都会经过交易委员会复核；评分达到{trigger_score}分只进入候选，不代表允许真实下单。榜单会持续显示入榜价、现价、委员会判断、冷却和候选状态。</div>
          </div>
        </div>
        """,
        unsafe_allow_html=True,
    )
    if not rows:
        st.info("交易机会榜单正在等待市场扫描数据。")
        return
    top1 = rows[0]
    top1_symbol = str(top1.get("symbol", "")).upper()
    top1_score = safe_score(top1.get("final_opportunity_score", top1.get("opportunity_score")))
    top1_risk = safe_score(top1.get("risk_score"))
    anchor_text = "当前查看" if committee_symbol == top1_symbol else "后台复核" if queue_symbol == top1_symbol else "未锚定"
    fast_text = "快速捕捉目标" if fast_target == top1_symbol else "等待快速确认"
    render_metric_grid(
        [
            ("榜首交易对象", top1_symbol, "green"),
            ("TOP1最终机会分", format_score(top1_score), "green" if safe_compare_gte(top1_score, trigger_score) else "yellow"),
            ("TOP1风险分", format_score(top1_risk), get_risk_class(top1_risk)),
            ("当前委员会对象", committee_symbol or "-", "blue"),
            ("锚定状态", anchor_text, "green" if anchor_text == "当前查看" else "yellow"),
            ("快速捕捉", fast_text, "green" if fast_text == "快速捕捉目标" else "yellow"),
            ("候选阈值", f"{trigger_score}分", "blue"),
        ]
    )
    render_top10_committee_precheck_summary(precheck_by_symbol)
    if committee_symbol != top1_symbol:
        if st.button(f"切换当前交易对象到 TOP1：{top1_symbol}", key=f"anchor_top1_{top1_symbol}_{compact}", use_container_width=True):
            set_current_symbol(top1_symbol, source="opportunity_board_click")
            st.rerun()
    st.markdown(
        '<div class="list-card"><div class="module-title">交易机会榜 TOP10</div>'
        '<div class="opp-row compact-five rank-head"><div>交易对象</div><div>入榜/现价</div><div>观察</div><div>涨跌/数据</div><div>机会/委员会</div></div>',
        unsafe_allow_html=True,
    )
    for index, row in enumerate(rows, start=1):
        medal_class = "gold" if index == 1 else "silver" if index == 2 else "bronze" if index == 3 else ""
        score = safe_score(row.get("final_opportunity_score", row.get("opportunity_score")))
        raw_score = safe_score(row.get("raw_opportunity_score"), score)
        risk = safe_score(row.get("risk_score"))
        risk_penalty = safe_score(row.get("risk_penalty"), 0)
        score_cap = safe_score(row.get("score_cap"), 100)
        status = str(row.get("opportunity_status", row.get("advice", "观察")))
        symbol = str(row.get("symbol", "")).upper()
        precheck = precheck_by_symbol.get(symbol, {})
        multi = multi_by_symbol.get(symbol, {})
        committee_summary = _opportunity_committee_summary(symbol, row, precheck, multi)
        row["committee_summary"] = committee_summary
        committee_action = str(committee_summary.get("final_action") or "等待判断")
        candidate_status = str(committee_summary.get("candidate_status") or "未进入")
        review_status = str(committee_summary.get("review_status") or "pending")
        reject_count = int(safe_score(committee_summary.get("reject_count"), 0) or 0)
        opportunity_round = int(safe_score(committee_summary.get("opportunity_round"), 1) or 1)
        removed_reason = str(committee_summary.get("removed_reason") or "")
        cooldown = committee_summary.get("cooldown_status") or {}
        cooldown_text = cooldown.get("reason") if cooldown.get("has_cooldown") else "无"
        ticker = market_cache.get_ticker(symbol) or {}
        entry_snapshot = row.get("entry_snapshot") if isinstance(row.get("entry_snapshot"), dict) else {}
        entry_price = safe_number(row.get("entry_price") or entry_snapshot.get("entry_price"))
        if entry_price is None:
            entry_price = safe_number(row.get("last_price") or row.get("current_price"))
        live_price = safe_number(ticker.get("last_price"), safe_number(row.get("last_price") or row.get("current_price")))
        change = safe_number(ticker.get("price_change_percent"), safe_number(row.get("price_change_percent")))
        live_change = ((live_price - entry_price) / entry_price * 100) if live_price is not None and entry_price not in {None, 0} else None
        change_class = "green" if (change is not None and change >= 0) else "red" if change is not None else "yellow"
        state = "当前查看" if symbol == committee_symbol else ("后台TOP1" if symbol == queue_symbol else "复核队列")
        selected_class = " selected-row" if symbol == committee_symbol else ""
        href = kline_href(symbol)
        st.markdown(
            f"""
            <div class="opp-row compact-five{selected_class}">
              <div>
                <a class="rank-link" href="{href}" target="_self"><div class="opp-symbol"><span class="rank-index {medal_class}">#{index}</span> {symbol}</div></a>
                <div class="opp-meta">{escape(str(row.get("current_market_state", "-")))} · {escape(str(row.get("opportunity_source", "-")))}</div>
                <div class="opp-meta">{escape(state)}</div>
              </div>
              <div>
                <div class="opp-meta">入榜价 {format_price(entry_price)}</div>
                <div class="opp-symbol">{format_price(live_price)}</div>
                <div class="opp-meta">入榜后 {format_percent(live_change)}</div>
              </div>
              <div>{watch_action_html(symbol, st.session_state.active_page, "实时交易机会榜")}</div>
              <div>
                <div class="{change_class}" style="font-weight:900;">24h {format_percent(change)}</div>
                <div class="opp-meta">复核：{escape(review_status)}</div>
                <div class="opp-meta">第{opportunity_round}轮｜否决{reject_count}次</div>
                <div class="opp-meta">冷却：{escape(str(cooldown_text))}</div>
                <div class="opp-meta">移除原因：{escape(removed_reason or "无")}</div>
                <div class="opp-meta">DeepSeek/Gemini：外部AI待补充</div>
              </div>
              <div>
                <div class="score-pill">终{format_score(score)} / 风{format_score(risk)}</div>
                <div class="opp-meta">原始{format_score(raw_score)}｜扣{format_score(risk_penalty)}｜封顶{format_score(score_cap)}</div>
                <div class="opp-meta">委员会：<span class="{get_opportunity_class(score)}">{escape(committee_action)}</span></div>
                <div class="opp-meta">候选：{escape(candidate_status)}｜{escape(status)}</div>
              </div>
            </div>
            """,
            unsafe_allow_html=True,
        )
        st.markdown(_render_score_sources(row), unsafe_allow_html=True)
    st.markdown("</div>", unsafe_allow_html=True)


# 榜单不使用 Streamlit 1秒 fragment 整块重绘；否则 tabs/列表会出现闪屏。
# 后台仍然每秒刷新缓存，页面交互或轻量前端接口可读取最新数据。
render_trade_opportunity_board_realtime = render_trade_opportunity_board


def render_watchlist(rankings: dict[str, list[dict[str, Any]]]) -> None:
    """渲染专业观察池。"""
    current_symbol = st.session_state.get("current_symbol", "BTCUSDT")
    summary = get_watchlist_summary()
    items = sorted(get_watchlist(), key=lambda item: (item.get("category") != "key_tracking", -(item.get("watch_score") or 0), item.get("symbol", "")))[:50]
    alerts = get_watchlist_alerts(8)
    candidates = get_watchlist_candidates_for_committee()[:8]

    st.markdown('<div class="list-card"><div class="module-title">观察池 / 重点币种跟踪系统</div><div class="module-desc">观察池只跟踪本地策略变化，不替代本地策略最终信号。</div>', unsafe_allow_html=True)
    render_watchlist_quick_controls(current_symbol, "market_watchlist", source="manual")
    cols = st.columns(3)
    summary_cards = [
        ("总观察", summary["total"]),
        ("手动观察", summary["manual"]),
        ("AI观察", summary["ai"]),
        ("重点跟踪", summary["key_tracking"]),
        ("高风险", summary["high_risk"]),
        ("信号失效", summary["expired"]),
    ]
    for index, (label, value) in enumerate(summary_cards):
        with cols[index % 3]:
            st.markdown(f'<div class="metric-box"><div class="metric-label">{label}</div><div class="metric-value yellow">{value}</div></div>', unsafe_allow_html=True)
    if st.button("清除非手动失效观察对象", key="watchlist_clear_expired", use_container_width=True):
        clear_expired_watchlist()
        st.success("已清理非手动来源的失效观察对象")

    if not items:
        st.markdown('<div class="pending">暂无观察对象。可以从当前交易对象或机会榜加入。</div></div>', unsafe_allow_html=True)
        return

    category_text = {"manual": "手动观察", "ai": "AI观察", "key_tracking": "重点跟踪", "high_risk": "高风险观察", "expired": "已失效观察"}
    for index, item in enumerate(items, start=1):
        symbol = item.get("symbol", "-")
        strategy = item.get("local_strategy") or {}
        tracking = item.get("tracking") or {}
        latest_alert = (item.get("alerts") or [{}])[0]
        data_quality = item.get("data_quality") or {}
        status = str(tracking.get("status", "持续观察"))
        status_color = _signal_color(status)
        source = escape(str(item.get("source", "manual")))
        category = category_text.get(str(item.get("category", "manual")), "手动观察")
        st.markdown(
            f"""
            <div class="module-card" style="margin-top:8px;">
              <div class="module-title">{kline_symbol_link(symbol, f"#{index} {symbol}")} <span class="{status_color}">· {escape(status)}</span></div>
              <div class="module-desc">点击币种可直接跳转到K线图区域。</div>
              <div class="module-desc">来源：{source}｜分类：{escape(category)}｜加入：{escape(str(item.get("added_time", "-")))}｜更新：{escape(str(item.get("last_update_time", "-")))}</div>
              <div class="watch-info-grid">
                <div class="watch-info-cell"><div class="watch-info-label">价格</div><div class="watch-info-value">{format_price(item.get("current_price"))}</div></div>
                <div class="watch-info-cell"><div class="watch-info-label">24h涨跌</div><div class="watch-info-value {_signal_color(format_percent(item.get("price_change_24h")))}">{format_percent(item.get("price_change_24h"))}</div></div>
                <div class="watch-info-cell"><div class="watch-info-label">本地策略</div><div class="watch-info-value {_signal_color(str(strategy.get("action", "观望")))}">{escape(str(strategy.get("action", "等待策略")))}</div></div>
                <div class="watch-info-cell"><div class="watch-info-label">策略类型</div><div class="watch-info-value yellow">{escape(str(strategy.get("strategy_name", "等待策略")))}</div></div>
                <div class="watch-info-cell"><div class="watch-info-label">置信度</div><div class="watch-info-value blue">{strategy.get("confidence", 0)} / 100</div></div>
                <div class="watch-info-cell"><div class="watch-info-label">风险</div><div class="watch-info-value yellow">{strategy.get("risk_score", 0)} / 100</div></div>
                <div class="watch-info-cell"><div class="watch-info-label">机会</div><div class="watch-info-value green">{strategy.get("opportunity_score", 0)} / 100</div></div>
                <div class="watch-info-cell"><div class="watch-info-label">观察评分</div><div class="watch-info-value green">{item.get("watch_score", 0)} / 100</div></div>
              </div>
              <div class="status-card" style="margin-top:8px;">
                状态：{escape(str(tracking.get("status_explanation", "等待策略同步。")).replace("等待下一轮策略跟踪", "等待策略同步"))}<br>
                等级：{escape(str(item.get("watch_level", "普通观察")))}｜{escape(str(item.get("watch_explanation", "本地策略数据同步中。")).replace("等待本地策略数据同步。", "本地策略数据同步中。"))}<br>
                提醒：{escape(str(latest_alert.get("content", "当前暂无提醒")))}<br>
                数据质量：{escape(str(data_quality.get("level", "poor")))}｜信号失效：{escape(str(strategy.get("invalid_condition", "等待确认")))}
              </div>
            </div>
            """,
            unsafe_allow_html=True,
        )
        st.markdown('<div class="watch-action-grid">', unsafe_allow_html=True)
        b1, b2, b3, b4 = st.columns(4)
        with b1:
            if st.button("切换", key=f"watch_switch_{symbol}_{index}", use_container_width=True):
                set_current_symbol(str(symbol))
        with b2:
            if st.button("标重点", key=f"watch_key_{symbol}_{index}", use_container_width=True):
                set_watchlist_category(str(symbol), "key_tracking")
                st.success(f"{symbol} 已标记为重点跟踪")
        with b3:
            if st.button("高风险", key=f"watch_risk_{symbol}_{index}", use_container_width=True):
                set_watchlist_category(str(symbol), "high_risk")
                st.warning(f"{symbol} 已移入高风险观察")
        with b4:
            if st.button("移除", key=f"watch_remove_{symbol}_{index}", use_container_width=True):
                remove_from_watchlist(str(symbol))
                st.warning(f"{symbol} 已移出观察池")
        st.markdown("</div>", unsafe_allow_html=True)
        with st.expander(f"{symbol} 观察详情", expanded=False):
            st.write("最近历史：")
            st.json((item.get("history") or [])[-8:])

    if alerts:
        st.markdown('<div class="module-title" style="margin-top:10px;">观察池提醒</div>', unsafe_allow_html=True)
        for alert in alerts:
            color = "red" if alert.get("level") == "高级提醒" else "yellow" if alert.get("level") == "中级提醒" else "blue"
            st.markdown(
                f'<div class="status-card" style="margin-top:6px;"><b class="{color}">{escape(str(alert.get("level", "提醒")))}</b>｜{escape(str(alert.get("time", "-")))}｜{escape(str(alert.get("symbol", "-")))}<br>{escape(str(alert.get("content", "")))}<br>原因：{escape(str(alert.get("reason", "")))}</div>',
                unsafe_allow_html=True,
            )
    if candidates:
        st.markdown('<div class="module-title" style="margin-top:10px;">交易委员会候选</div>', unsafe_allow_html=True)
        for row in candidates:
            st.markdown(
                f'<div class="status-card" style="margin-top:6px;"><b>{escape(str(row.get("symbol", "-")))}</b>｜观察评分 {row.get("watch_score", 0)}｜{escape(str(row.get("local_strategy_action", "-")))}｜{escape(str(row.get("strategy_name", "-")))}<br>{escape(str(row.get("main_reason", "")))}<br>主要风险：{escape(str(row.get("main_risk", "")))}</div>',
                unsafe_allow_html=True,
            )
    st.markdown("</div>", unsafe_allow_html=True)


def render_market(rankings: dict[str, list[dict[str, Any]]] | None) -> None:
    """市场页。"""
    rankings = market_cache.get_rankings() or rankings or {}
    render_page_head("market")
    try:
        auto_add_from_rankings(rankings)
    except Exception as exc:
        print(f"[观察池] AI自动加入候选失败 error={repr(exc)}")
    render_fast_opportunity_panel()
    render_trade_opportunity_board(rankings)
    st.markdown('<div class="app-shell">', unsafe_allow_html=True)
    basic_tabs = st.tabs(["涨幅榜", "跌幅榜", "成交量榜", "观察池"])
    with basic_tabs[0]:
        render_rank_list("涨幅榜 TOP10", rankings.get("gainers", []), "gainers")
    with basic_tabs[1]:
        render_rank_list("跌幅榜 TOP10", rankings.get("losers", []), "losers")
    with basic_tabs[2]:
        render_rank_list("成交量榜 TOP10", rankings.get("volume", []), "volume")
    with basic_tabs[3]:
        render_watchlist(rankings)
    st.markdown("</div>", unsafe_allow_html=True)

    st.markdown('<div class="app-shell">', unsafe_allow_html=True)
    tabs = st.tabs(["强势币榜", "弱势币榜", "多头机会榜", "空头机会榜", "异动币榜", "高风险榜"])
    with tabs[0]:
        render_opportunity_list("强势币榜 TOP10", rankings.get("strong", []), "strong")
    with tabs[1]:
        render_opportunity_list("弱势币榜 TOP10", rankings.get("weak", []), "weak")
    with tabs[2]:
        render_opportunity_list("多头机会榜 TOP10", rankings.get("long_opportunities", []), "long_opportunities")
    with tabs[3]:
        render_opportunity_list("空头机会榜 TOP10", rankings.get("short_opportunities", []), "short_opportunities")
    with tabs[4]:
        render_opportunity_list("异动币榜 TOP10", rankings.get("abnormal", []), "abnormal")
    with tabs[5]:
        render_opportunity_list("高风险榜 TOP10", rankings.get("high_risk", []), "high_risk")
    st.markdown("</div>", unsafe_allow_html=True)


# 市场页包含多组 tabs 和榜单，整页 1秒 fragment 会造成明显闪屏。
render_market_realtime = render_market


def add_ma_trace(fig: go.Figure, x_values: list[Any], ma_values: list[float | None], name: str, color: str) -> None:
    """向图表添加均线。"""
    fig.add_trace(
        go.Scatter(x=x_values, y=ma_values, mode="lines", name=name, line={"color": color, "width": 1.25}, connectgaps=False),
        row=1,
        col=1,
    )


def build_kline_figure(symbol: str, interval: str, rows: list[dict[str, Any]], visible_mas: list[str], follow_latest: bool, chart_interactive: bool) -> tuple[go.Figure, dict[str, Any]]:
    """构建专业 K线图。"""
    x_values = [row["open_datetime"] for row in rows]
    closes = [row["close"] for row in rows]
    ma_map = {
        "MA5": calculate_ma(closes, 5),
        "MA10": calculate_ma(closes, 10),
        "MA20": calculate_ma(closes, 20),
        "MA60": calculate_ma(closes, 60),
        "MA120": calculate_ma(closes, 120),
    }
    ma_colors = {"MA5": "#F0B90B", "MA10": "#3B82F6", "MA20": "#A78BFA", "MA60": "#22C55E", "MA120": "#F97316"}
    support, resistance = detect_support_resistance(rows)
    cross = detect_cross(rows, ma_map["MA20"], ma_map["MA60"])
    state = analyze_kline_state(rows, ma_map["MA20"], ma_map["MA60"])
    last_close = closes[-1]

    fig = make_subplots(rows=2, cols=1, shared_xaxes=True, vertical_spacing=0.04, row_heights=[0.74, 0.26])
    hover_text = [
        (
            f"时间：{row['open_datetime'].strftime('%Y-%m-%d %H:%M:%S')}<br>"
            f"开盘：{format_price(row['open'])}<br>最高：{format_price(row['high'])}<br>"
            f"最低：{format_price(row['low'])}<br>收盘：{format_price(row['close'])}<br>成交量：{format_compact(row['volume'])}"
        )
        for row in rows
    ]
    fig.add_trace(
        go.Candlestick(
            x=x_values,
            open=[row["open"] for row in rows],
            high=[row["high"] for row in rows],
            low=[row["low"] for row in rows],
            close=closes,
            name="K线",
            increasing={"line": {"color": "#00C087"}, "fillcolor": "#00C087"},
            decreasing={"line": {"color": "#F6465D"}, "fillcolor": "#F6465D"},
            text=hover_text,
            hoverinfo="text",
        ),
        row=1,
        col=1,
    )
    for name in visible_mas:
        if name in ma_map:
            add_ma_trace(fig, x_values, ma_map[name], name, ma_colors[name])

    volume_colors = ["#00C087" if row["close"] >= row["open"] else "#F6465D" for row in rows]
    fig.add_trace(go.Bar(x=x_values, y=[row["volume"] for row in rows], name="Volume", marker={"color": volume_colors, "opacity": 0.58}), row=2, col=1)

    def add_price_guide(price: float | None, label: str, color: str, dash: str, yshift: int) -> None:
        if price is None:
            return
        fig.add_hline(y=price, line_dash=dash, line_color=color, line_width=1, row=1, col=1)
        fig.add_annotation(
            x=0.985,
            xref="paper",
            y=price,
            yref="y",
            text=f"{label} {format_price(price)}",
            showarrow=True,
            arrowhead=2,
            arrowsize=1,
            arrowwidth=1,
            arrowcolor=color,
            ax=-72,
            ay=yshift,
            bgcolor="rgba(15,23,42,.94)",
            bordercolor=color,
            borderwidth=1,
            font={"color": "#FFFFFF", "size": 10},
        )

    add_price_guide(resistance, "压力", "#F6465D", "dash", -24)
    add_price_guide(last_close, "现价", "#F0B90B", "dot", 0)
    add_price_guide(support, "支撑", "#22C55E", "dash", 24)

    if cross["type"] != "none":
        marker_color = "#00C087" if cross["type"] == "golden" else "#F6465D"
        marker_symbol = "triangle-up" if cross["type"] == "golden" else "triangle-down"
        fig.add_trace(
            go.Scatter(x=[cross["time"]], y=[cross["price"]], mode="markers+text", name=cross["label"], text=[cross["label"]], textposition="top center", marker={"color": marker_color, "size": 12, "symbol": marker_symbol}),
            row=1,
            col=1,
        )

    layout_range = None
    if follow_latest and not chart_interactive and len(x_values) > 80:
        layout_range = [x_values[-80], x_values[-1]]
    fig.update_layout(
        title=f"{symbol} K线图（{interval}）",
        autosize=True,
        height=570,
        paper_bgcolor="#050B14",
        plot_bgcolor="#050B14",
        font={"color": "#E5E7EB", "size": 11},
        margin={"l": 42, "r": 16, "t": 38, "b": 24},
        hovermode="x unified",
        hoverlabel={"bgcolor": "#0F172A", "bordercolor": "#334155", "font": {"color": "#FFFFFF"}},
        dragmode="zoom" if chart_interactive else False,
        uirevision=f"{symbol}-{interval}",
        legend={"orientation": "h", "yanchor": "bottom", "y": 1.01, "xanchor": "left", "x": 0},
        xaxis_rangeslider_visible=False,
    )
    fig.update_xaxes(
        showgrid=True,
        gridcolor="rgba(148,163,184,.12)",
        rangeslider_visible=False,
        range=layout_range,
        showspikes=True,
        spikemode="across",
        spikesnap="cursor",
        spikecolor="rgba(229,231,235,.55)",
        spikethickness=1,
    )
    fig.update_yaxes(showgrid=True, gridcolor="rgba(148,163,184,.12)", autorange=True, fixedrange=False, showspikes=True, spikecolor="rgba(229,231,235,.55)", row=1, col=1)
    fig.update_yaxes(showgrid=True, gridcolor="rgba(148,163,184,.10)", autorange=True, fixedrange=False, row=2, col=1)
    meta = {"support": support, "resistance": resistance, "cross": cross, "state": state, "last_close": last_close, "ma20": ma_map["MA20"][-1], "ma60": ma_map["MA60"][-1]}
    return fig, meta


def build_kline_meta(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """只计算 K线摘要信息，不触发 Python Plotly 图形构建。"""
    closes = [row["close"] for row in rows]
    ma20 = calculate_ma(closes, 20)
    ma60 = calculate_ma(closes, 60)
    support, resistance = detect_support_resistance(rows)
    cross = detect_cross(rows, ma20, ma60)
    state = analyze_kline_state(rows, ma20, ma60)
    return {
        "support": support,
        "resistance": resistance,
        "cross": cross,
        "state": state,
        "last_close": closes[-1] if closes else None,
        "ma20": ma20[-1] if ma20 else None,
        "ma60": ma60[-1] if ma60 else None,
    }


def frontend_kline_html(symbol: str, interval: str, visible_mas: list[str], chart_interactive: bool, follow_latest: bool) -> str:
    """生成前端自刷新的 Plotly K线组件。"""
    visible_mas_json = json.dumps(visible_mas, ensure_ascii=False)
    plotly_js = get_plotlyjs()
    return f"""
    <style>
      body {{ margin:0; background:#050B14; font-family:Arial,'Microsoft YaHei',sans-serif; }}
      #chart-wrap {{ height:620px; border:1px solid #1F2937; border-radius:14px; background:#050B14; overflow:hidden; }}
      #chart {{ width:100%; height:100%; }}
      #status {{ position:absolute; top:8px; right:10px; z-index:5; color:#9CA3AF; font-size:11px; background:rgba(15,23,42,.78); border:1px solid #334155; border-radius:8px; padding:4px 7px; }}
    </style>
    <div id="chart-wrap">
      <div id="status">K线前端连接中...</div>
      <div id="chart"></div>
    </div>
    <script>{plotly_js}</script>
    <script>
      const symbol = "{symbol}";
      const interval = "{interval}";
      const visibleMas = {visible_mas_json};
      const chartInteractive = {str(bool(chart_interactive)).lower()};
      const followLatest = {str(bool(follow_latest)).lower()};
      const statusEl = document.getElementById("status");
      let rows = [];
      let klineMessage = "";
      let userInteracted = false;
      let listenerBound = false;
      const chart = document.getElementById("chart");
      {frontend_api_client_js("fetchKlineJson")}

      function fmtPrice(v) {{
        const n = Number(v);
        if (!Number.isFinite(n)) return "-";
        if (n >= 1000) return n.toLocaleString(undefined, {{minimumFractionDigits:2, maximumFractionDigits:2}});
        if (n >= 1) return n.toLocaleString(undefined, {{minimumFractionDigits:4, maximumFractionDigits:4}});
        return n.toFixed(8).replace(/0+$/,'').replace(/\\.$/,'');
      }}
      function ma(values, period) {{
        return values.map((_, i) => {{
          if (i + 1 < period) return null;
          let sum = 0;
          for (let j = i - period + 1; j <= i; j++) sum += values[j];
          return sum / period;
        }});
      }}
      function supportResistance(lows, highs) {{
        const recentLows = lows.slice(-80);
        const recentHighs = highs.slice(-80);
        return {{
          support: Math.min(...recentLows),
          resistance: Math.max(...recentHighs)
        }};
      }}
      function buildTraces() {{
        const x = rows.map(r => new Date(r.openTime));
        const open = rows.map(r => r.open);
        const high = rows.map(r => r.high);
        const low = rows.map(r => r.low);
        const close = rows.map(r => r.close);
        const volume = rows.map(r => r.volume);
        const traces = [{{
          type:"candlestick",
          x, open, high, low, close,
          name:"K线",
          increasing:{{line:{{color:"#00C087"}}, fillcolor:"#00C087"}},
          decreasing:{{line:{{color:"#F6465D"}}, fillcolor:"#F6465D"}},
          hovertext: rows.map(r => `时间：${{new Date(r.openTime).toLocaleString()}}<br>开盘：${{fmtPrice(r.open)}}<br>最高：${{fmtPrice(r.high)}}<br>最低：${{fmtPrice(r.low)}}<br>收盘：${{fmtPrice(r.close)}}<br>成交量：${{r.volume.toLocaleString()}}`),
          hoverinfo:"text",
          xaxis:"x",
          yaxis:"y"
        }}];
        const maConfig = {{
          MA5:[5,"#F0B90B"], MA10:[10,"#3B82F6"], MA20:[20,"#A78BFA"], MA60:[60,"#22C55E"], MA120:[120,"#F97316"]
        }};
        for (const key of visibleMas) {{
          if (!maConfig[key]) continue;
          traces.push({{type:"scatter", mode:"lines", x, y:ma(close, maConfig[key][0]), name:key, line:{{color:maConfig[key][1], width:1.25}}, connectgaps:false, xaxis:"x", yaxis:"y"}});
        }}
        traces.push({{
          type:"bar", x, y:volume, name:"Volume",
          marker:{{color: rows.map(r => r.close >= r.open ? "#00C087" : "#F6465D"), opacity:.58}},
          xaxis:"x", yaxis:"y2"
        }});
        return traces;
      }}
      function buildLayout() {{
        const x = rows.map(r => new Date(r.openTime));
        const high = rows.map(r => r.high);
        const low = rows.map(r => r.low);
        const close = rows.map(r => r.close);
        const sr = supportResistance(low, high);
        const last = close[close.length - 1];
        const range = followLatest && !userInteracted && x.length > 80 ? [x[x.length - 80], x[x.length - 1]] : undefined;
        return {{
          title:{{text:`${{symbol}} K线图（${{interval}}）`, font:{{color:"#E5E7EB", size:15}}}},
          paper_bgcolor:"#050B14", plot_bgcolor:"#050B14", font:{{color:"#E5E7EB", size:11}},
          margin:{{l:42,r:70,t:38,b:28}},
          dragmode: chartInteractive ? "pan" : false,
          hovermode:"x unified",
          hoverlabel:{{bgcolor:"#0F172A", bordercolor:"#334155", font:{{color:"#FFFFFF"}}}},
          showlegend:true,
          legend:{{orientation:"h", y:1.03, x:0}},
          uirevision:`${{symbol}}-${{interval}}`,
          grid:{{rows:2, columns:1, subplots:[["xy"],["xy2"]], roworder:"top to bottom"}},
          xaxis:{{domain:[0,1], anchor:"y", rangeslider:{{visible:false}}, showgrid:true, gridcolor:"rgba(148,163,184,.12)", range, showspikes:true, spikemode:"across", spikesnap:"cursor"}},
          yaxis:{{domain:[.28,1], anchor:"x", showgrid:true, gridcolor:"rgba(148,163,184,.12)", autorange:true, fixedrange:false}},
          yaxis2:{{domain:[0,.22], anchor:"x", showgrid:true, gridcolor:"rgba(148,163,184,.10)", autorange:true, fixedrange:false}},
          shapes:[
            {{type:"line", xref:"paper", x0:0, x1:1, yref:"y", y0:sr.resistance, y1:sr.resistance, line:{{color:"#F6465D", dash:"dash", width:1}}}},
            {{type:"line", xref:"paper", x0:0, x1:1, yref:"y", y0:last, y1:last, line:{{color:"#F0B90B", dash:"dot", width:1}}}},
            {{type:"line", xref:"paper", x0:0, x1:1, yref:"y", y0:sr.support, y1:sr.support, line:{{color:"#22C55E", dash:"dash", width:1}}}}
          ],
          annotations:[
            {{xref:"paper", x:.985, yref:"y", y:sr.resistance, text:`压力 ${{fmtPrice(sr.resistance)}}`, showarrow:true, ax:-76, ay:-22, bgcolor:"rgba(15,23,42,.94)", bordercolor:"#F6465D", font:{{color:"#fff", size:10}}}},
            {{xref:"paper", x:.985, yref:"y", y:last, text:`现价 ${{fmtPrice(last)}}`, showarrow:true, ax:-76, ay:0, bgcolor:"rgba(15,23,42,.94)", bordercolor:"#F0B90B", font:{{color:"#fff", size:10}}}},
            {{xref:"paper", x:.985, yref:"y", y:sr.support, text:`支撑 ${{fmtPrice(sr.support)}}`, showarrow:true, ax:-76, ay:22, bgcolor:"rgba(15,23,42,.94)", bordercolor:"#22C55E", font:{{color:"#fff", size:10}}}}
          ]
        }};
      }}
      function config() {{
        return {{scrollZoom: chartInteractive, displayModeBar:false, responsive:true, doubleClick:"reset", staticPlot: !chartInteractive}};
      }}
      async function fetchKlines() {{
        const data = await fetchKlineJson(`/api/klines?symbol=${{encodeURIComponent(symbol)}}&interval=${{encodeURIComponent(interval)}}`);
        klineMessage = data.message || "";
        const payloadRows = Array.isArray(data) ? data : (data.rows || []);
        rows = payloadRows.map(r => ({{openTime:r.openTime, open:Number(r.open), high:Number(r.high), low:Number(r.low), close:Number(r.close), volume:Number(r.volume), closeTime:r.closeTime}}));
      }}
      async function fetchTickerPatch() {{
        const data = await fetchKlineJson(`/api/ticker?symbol=${{encodeURIComponent(symbol)}}`);
        const price = Number(data.last_price);
        if (!rows.length || !Number.isFinite(price)) return;
        const last = rows[rows.length - 1];
        last.close = price;
        last.high = Math.max(last.high, price);
        last.low = Math.min(last.low, price);
      }}
      async function draw() {{
        if (!rows.length) return;
        await Plotly.react(chart, buildTraces(), buildLayout(), config());
        if (!listenerBound && chart.on) {{
          chart.on("plotly_relayout", () => {{ userInteracted = true; }});
          listenerBound = true;
        }}
      }}
      async function init() {{
        try {{
          await fetchKlines();
          if (rows.length) {{
            await draw();
            statusEl.textContent = "K线实时更新中";
          }} else {{
            statusEl.textContent = klineMessage || "等待本地K线缓存...";
          }}
        }} catch (err) {{
          statusEl.textContent = err && err.message ? err.message : "K线获取失败，正在重试";
        }}
      }}
      async function fastLoop() {{
        try {{
          if (!rows.length) await fetchKlines();
          await fetchTickerPatch();
          await draw();
          statusEl.textContent = rows.length ? `实时：${{new Date().toLocaleTimeString()}}` : (klineMessage || "K线正在后台刷新");
        }} catch (err) {{
          statusEl.textContent = err && err.message ? err.message : "实时更新失败，重试中";
        }}
      }}
      async function slowLoop() {{
        try {{
          await fetchKlines();
          await draw();
        }} catch (err) {{}}
      }}
      init();
      setInterval(fastLoop, 1000);
      setInterval(slowLoop, 15000);
    </script>
    """


def render_kline_live_status(symbol: str) -> None:
    """只刷新 K线实时状态，不重建图表。"""
    interval = market_cache.get_kline_interval()
    ticker = market_cache.get_ticker(symbol)
    snapshot = market_cache.snapshot()
    price = format_price(ticker["last_price"]) if ticker else "正在获取"
    st.markdown(
        f"""<div class="kline-meta-grid">
        <div class="kline-meta-box"><div class="metric-label">实时价格</div><div class="metric-value yellow">{price}</div></div>
        <div class="kline-meta-box"><div class="metric-label">K线缓存</div><div class="metric-value blue">{snapshot.get("kline_last_update_time", "初始化中")}</div></div>
        <div class="kline-meta-box"><div class="metric-label">当前周期</div><div class="metric-value">{interval}</div></div>
        <div class="kline-meta-box"><div class="metric-label">图表刷新策略</div><div class="metric-value green">操作优先</div></div>
        </div>""",
        unsafe_allow_html=True,
    )


def render_kline_system(symbol: str) -> None:
    """渲染专业实时K线系统。"""
    snapshot = market_cache.snapshot()
    interval = market_cache.get_kline_interval()
    if live_refresh_due(f"ticker:{symbol}", 2.0):
        try:
            refresh_symbol_now(symbol)
            snapshot = market_cache.snapshot()
        except Exception as exc:
            market_cache.set_ticker_error(f"K线现价刷新失败：{exc!r}")
            snapshot = market_cache.snapshot()
    rows = market_cache.get_klines(symbol, interval)
    if not rows or live_refresh_due(f"kline:{symbol}:{interval}", 8.0):
        try:
            refresh_klines_now(symbol, interval)
            rows = market_cache.get_klines(symbol, interval)
            snapshot = market_cache.snapshot()
        except Exception as exc:
            market_cache.set_kline_error(f"K线服务端刷新失败：{exc!r}")
            snapshot = market_cache.snapshot()
    st.markdown('<div id="kline-area"></div>', unsafe_allow_html=True)
    st.markdown(
        f"""<div class="app-shell"><div class="kline-card">
        <div class="kline-head">
          <div><div class="kline-title">专业实时K线系统</div><div class="module-desc">{symbol}｜后台更新｜图表操作优先，避免缩放闪屏</div></div>
          <div class="kline-status">状态：{snapshot.get("kline_status", "初始化中")}<br>更新：{snapshot.get("kline_last_update_time", "初始化中")}</div>
        </div>""",
        unsafe_allow_html=True,
    )
    st.radio("K线周期", KLINE_INTERVALS, key="kline_interval", horizontal=True, on_change=on_kline_interval_change, label_visibility="collapsed")
    c1, c2 = st.columns([1.7, 1])
    with c1:
        st.multiselect("均线显示", MA_OPTIONS, key="ma_visibility", placeholder="选择需要显示的均线")
    with c2:
        st.toggle("跟随最新", key="follow_latest")
        st.button("回到最新", on_click=reset_follow_latest, use_container_width=True)
    st.toggle("进入K线操作模式", key="chart_interactive", help="开启后启用缩放、拖动和十字悬浮；关闭时优先保证手机页面滚动顺畅。")
    if not rows:
        error = snapshot.get("kline_last_error") or "正在获取K线数据"
        left, right = st.columns([2.15, .85])
        with left:
            st.markdown(f'<div class="pending">{escape(str(error))}</div>', unsafe_allow_html=True)
        with right:
            st.markdown(f'<div class="side-stack"><div class="summary-card"><div class="summary-label">K线缓存</div><div class="summary-value yellow">{escape(str(error))}</div></div><div class="summary-card"><div class="summary-label">当前周期</div><div class="summary-value">{interval}</div></div><div class="summary-card"><div class="summary-label">历史查看模式</div><div class="summary-value blue">{"跟随最新" if st.session_state.follow_latest else "手动浏览"}</div></div></div>', unsafe_allow_html=True)
        st.markdown("</div></div>", unsafe_allow_html=True)
        return
    fig, meta = build_kline_figure(symbol, interval, rows, st.session_state.ma_visibility, st.session_state.follow_latest, st.session_state.chart_interactive)
    cross = meta["cross"]
    cross_text = cross["label"] if cross["type"] == "none" else f"{cross['label']}｜{cross['time'].strftime('%Y-%m-%d %H:%M:%S')}"
    left, right = st.columns([2.15, .85])
    with left:
        st.plotly_chart(fig, use_container_width=True, config={"scrollZoom": bool(st.session_state.chart_interactive), "displayModeBar": False, "responsive": True})
    with right:
        st.markdown(
            f"""<div class="side-stack">
            <div class="summary-card"><div class="summary-label">当前价格</div><div class="summary-value">{format_price(meta["last_close"])}</div></div>
            <div class="summary-card"><div class="summary-label">K线状态</div><div class="summary-value yellow">{meta["state"]}</div></div>
            <div class="summary-card"><div class="summary-label">MA20</div><div class="summary-value">{format_price(meta["ma20"])}</div></div>
            <div class="summary-card"><div class="summary-label">MA60</div><div class="summary-value">{format_price(meta["ma60"])}</div></div>
            <div class="summary-card"><div class="summary-label">支撑位</div><div class="summary-value green">{format_price(meta["support"]) if meta["support"] else "待确认"}</div></div>
            <div class="summary-card"><div class="summary-label">压力位</div><div class="summary-value red">{format_price(meta["resistance"]) if meta["resistance"] else "待确认"}</div></div>
            <div class="summary-card"><div class="summary-label">均线交叉</div><div class="summary-value">{cross_text}</div></div>
            <div class="summary-card"><div class="summary-label">历史查看模式</div><div class="summary-value blue">{"跟随最新" if st.session_state.follow_latest else "手动浏览"}</div></div>
            </div>""",
            unsafe_allow_html=True,
        )
    st.markdown("</div></div>", unsafe_allow_html=True)


def _orderbook_level_html(level: dict[str, Any], side: str, max_quantity: float, large_order: dict[str, Any] | None) -> str:
    """生成单行盘口 HTML。"""
    quantity = float(level.get("quantity", 0) or 0)
    width = 0 if max_quantity <= 0 else min(100, quantity / max_quantity * 100)
    side_class = "ask" if side == "ask" else "bid"
    color_class = "red" if side == "ask" else "green"
    is_large = bool(large_order and large_order.get("price_text") == level.get("price_text"))
    large_class = " large" if is_large else ""
    return (
        f'<div class="orderbook-row{large_class}">'
        f'<div class="depth-bar {side_class}" style="width:{width:.2f}%;"></div>'
        f'<div class="ob-cell {color_class}">{level.get("price_text", "-")}</div>'
        f'<div class="ob-cell ob-right">{level.get("quantity_text", "-")}</div>'
        f'<div class="ob-cell ob-right">{level.get("cumulative_text", "-")}</div>'
        "</div>"
    )


def _large_order_text(title: str, large_order: dict[str, Any] | None) -> str:
    """格式化大单监控文本。"""
    if not large_order:
        return f"{title}：暂无异常"
    distance = large_order.get("distance_percent")
    distance_text = f"{distance:.2f}%" if distance is not None else "待确认"
    return f'{title}：{large_order.get("price_text")} / {large_order.get("quantity_text")} / 距离 {distance_text}'


def _whale_order_text(order: dict[str, Any] | None, empty_text: str) -> str:
    """格式化大单订单文本，兼容后端不同字段。"""
    if not order:
        return empty_text
    amount = order.get("amount")
    if amount in (None, "") and not order.get("amount_text"):
        return empty_text
    price_text = order.get("price_text") or format_price(order.get("price"))
    quantity_text = order.get("quantity_text") or format_compact(order.get("quantity"))
    amount_text = order.get("amount_text") or format_compact(amount)
    return f"{price_text} / {quantity_text} / {amount_text}"


def _whale_trade_rows_html(whale: dict[str, Any] | None) -> str:
    """渲染最新大单或最新成交列表。"""
    if not whale:
        return '<div class="status-card">大单数据正在初始化。</div>'
    rows = list(whale.get("latest") or [])
    showing_recent = False
    if not rows:
        rows = list(whale.get("recent_trades") or [])
        showing_recent = bool(rows)
    rows = rows[:6]
    if not rows:
        return '<div class="status-card">当前暂无达到阈值的大单，等待下一轮成交同步。</div>'
    note = '<div class="status-card">当前未触发大单阈值，以下显示最新成交。</div>' if showing_recent else ""
    body = "".join(
        f"""<div class="orderbook-row">
          <div class="{ "green" if str(row.get("direction")) == "主动买入" else "red" }">{escape(str(row.get("time", "-")))}</div>
          <div>{escape(str(row.get("direction", "-")))}</div>
          <div class="ob-right">{escape(str(row.get("price_text") or format_price(row.get("price"))))}</div>
          <div class="ob-right">{escape(str(row.get("amount_text") or format_compact(row.get("amount"))))}</div>
        </div>"""
        for row in rows
    )
    return (
        note
        + '<div class="orderbook-row header"><div>时间</div><div>方向</div><div class="ob-right">价格</div><div class="ob-right">金额</div></div>'
        + body
    )


def _signal_color(value: str) -> str:
    """根据中文信号选择颜色类。"""
    if value in {"强多", "偏多", "顺势做多", "轻仓试多", "上升趋势", "突破", "回踩确认", "加速上涨", "金叉", "多头延续", "极强", "偏强", "资金流入", "健康上涨", "健康下跌", "空头回补", "安全", "较安全", "低", "低风险", "偏低风险", "可交易", "轻仓可试", "支持交易", "轻仓支持"}:
        return "green"
    if value in {"强空", "偏空", "顺势做空", "轻仓试空", "下降趋势", "跌破", "加速下跌", "极高风险", "高风险", "死叉", "空头延续", "极弱", "偏弱", "资金恐慌", "危险上涨", "多头拥挤", "空头拥挤", "恐慌下跌", "空头挤压风险", "多头踩踏风险", "高风险双向震荡", "高风险上涨", "高风险下跌", "多空双杀风险", "疑似诱多", "疑似诱空", "极端风险", "高", "极高", "不建议开仓", "禁止开仓", "反对交易"}:
        return "red"
    if value in {"中等风险", "横盘震荡", "假突破", "观望", "不建议追多", "不建议追空", "资金观望", "资金过热", "高风险震荡", "中性", "正常", "中", "震荡观望", "谨慎交易"}:
        return "yellow"
    return "blue"


def _safe_committee_text(value: Any, limit: int = 260) -> str:
    """Redact keys/URLs/code-like blobs before rendering committee text."""
    text = str(value or "")
    leak_markers = [
        "HTTPSConnectionPool",
        "NameResolutionError",
        "Max retries exceeded",
        "generateContent",
        "chat/completions",
        "api.deepseek.com",
        "generativelanguage.googleapis.com",
        "api_key",
        "x-goog-api-key",
        "Authorization",
        "Traceback",
    ]
    if any(marker.lower() in text.lower() for marker in leak_markers):
        return "外部AI暂不可用，已按观望处理；本地委员会继续运行。"
    text = re.sub(r"([?&]key=)[^&\s)\"']+", r"\1[已隐藏]", text, flags=re.IGNORECASE)
    text = re.sub(r"(Authorization:\s*Bearer\s+)[^\s,;]+", r"\1[已隐藏]", text, flags=re.IGNORECASE)
    text = re.sub(r"(x-goog-api-key['\"]?\s*[:=]\s*['\"]?)[^'\"\s,;]+", r"\1[已隐藏]", text, flags=re.IGNORECASE)
    text = re.sub(r"https?://[^\s)\"']+", "[外部接口地址已隐藏]", text)
    text = re.sub(r"\b[A-Za-z0-9_\-]{24,}\b", "[敏感片段已隐藏]", text)
    text = re.sub(r"```.*?```", "[代码块已隐藏]", text, flags=re.DOTALL)
    text = " ".join(text.replace("\n", " ").split())
    if len(text) > limit:
        text = text[:limit].rstrip() + "..."
    return text


def _render_numbered(items: list[str]) -> str:
    """渲染紧凑编号解释。"""
    return "".join(f"<li>{escape(_safe_committee_text(item))}</li>" for item in items)


def _html_no_code_block(html: str) -> str:
    """Remove leading indentation so Markdown never treats HTML as a code block."""
    return "\n".join(line.lstrip() for line in dedent(str(html)).splitlines()).strip()


def render_html(html: str) -> None:
    st.markdown(_html_no_code_block(html), unsafe_allow_html=True)


def _get_cached_snapshot_validation_report() -> dict[str, Any]:
    """Build snapshot validation report at most once per minute per session."""
    cache_key = "_cognition_snapshot_validation_report_cache"
    now = time.time()
    cached = st.session_state.get(cache_key)
    if isinstance(cached, dict) and now - float(cached.get("created_at", 0) or 0) < 60:
        return cached.get("report") or {}
    try:
        report = build_snapshot_validation_report()
        try:
            save_snapshot_validation_report(report)
        except Exception:
            pass
    except Exception as exc:
        report = {
            "total_snapshots": 0,
            "valid_snapshots": 0,
            "invalid_snapshots": 0,
            "valid_ratio": 0,
            "avg_quality_score": 0,
            "avg_data_integrity_score": 0,
            "avg_confidence": 0,
            "latest_snapshot_time": "",
            "latest_state_code": "",
            "snapshot_dir_size_mb": 0,
            "disk_risk_status": "UNKNOWN",
            "experience_sample_compatible_ratio": 0,
            "recommendation": f"快照验收报告生成失败：{exc}",
            "missing_field_top10": {},
            "error_top10": {},
            "warning_top10": {},
            "state_code_distribution": {},
        }
    st.session_state[cache_key] = {"created_at": now, "report": report}
    return report


def _top_counter_rows(counter: dict[str, Any], key_label: str) -> list[dict[str, Any]]:
    return [{key_label: key, "次数": value} for key, value in (counter or {}).items()]


def render_cognition_snapshot_validation_panel() -> None:
    """Render market cognition snapshot acceptance summary."""
    report = _get_cached_snapshot_validation_report()
    total = int(report.get("total_snapshots") or 0)
    disk_status = str(report.get("disk_risk_status") or "OK")
    disk_class = "green" if disk_status == "OK" else "yellow" if disk_status == "WARNING" else "red"
    compatible_ratio = float(report.get("experience_sample_compatible_ratio") or 0)
    compatible_class = "green" if compatible_ratio >= 95 else "yellow" if compatible_ratio >= 70 else "red"

    render_html(
        f"""
        <div class="app-shell">
          <div class="module-card">
            <div class="module-title">市场认知快照验收</div>
            <div class="module-desc">{escape(str(report.get("recommendation") or "等待快照验收结果。"))}</div>
          </div>
        </div>
        """
    )
    render_metric_grid(
        [
            ("今日快照", str(report.get("today_snapshots", 0)), ""),
            ("总快照", str(total), ""),
            ("有效快照", str(report.get("valid_snapshots", 0)), "green"),
            ("无效快照", str(report.get("invalid_snapshots", 0)), "red" if int(report.get("invalid_snapshots") or 0) else ""),
            ("有效率", f"{float(report.get('valid_ratio') or 0):.1f}%", "green" if float(report.get("valid_ratio") or 0) >= 95 else "yellow"),
            ("平均质量分", f"{float(report.get('avg_quality_score') or 0):.1f}", "green" if float(report.get("avg_quality_score") or 0) >= 80 else "yellow"),
            ("平均完整度", f"{float(report.get('avg_data_integrity_score') or 0):.1f}", ""),
            ("平均置信度", f"{float(report.get('avg_confidence') or 0):.1f}", ""),
            ("最近快照", str(report.get("latest_snapshot_time") or "暂无"), ""),
            ("最近状态码", str(report.get("latest_state_code") or "暂无"), ""),
            ("目录大小", f"{float(report.get('snapshot_dir_size_mb') or 0):.2f} MB", disk_class),
            ("磁盘风险", disk_status, disk_class),
            ("样本兼容率", f"{compatible_ratio:.1f}%", compatible_class),
        ]
    )
    if total <= 0:
        st.info("暂无市场认知快照，请等待系统运行生成。")
        return
    if disk_status == "DANGER":
        st.error("快照目录过大，建议检查写入频率和保留天数。验证器不会自动删除任何数据。")
    elif disk_status == "WARNING":
        st.warning("快照目录已超过100MB，建议继续观察增长速度。")

    with st.expander("缺失字段排行", expanded=False):
        rows = _top_counter_rows(report.get("missing_field_top10") or {}, "缺失字段")
        if rows:
            st.dataframe(rows, use_container_width=True, hide_index=True)
        else:
            st.success("未发现主要缺失字段。")
    with st.expander("错误排行", expanded=False):
        rows = _top_counter_rows(report.get("error_top10") or {}, "错误")
        if rows:
            st.dataframe(rows, use_container_width=True, hide_index=True)
        else:
            st.success("未发现主要错误。")
    with st.expander("状态码分布", expanded=False):
        rows = _top_counter_rows(report.get("state_code_distribution") or {}, "状态码")[:30]
        if rows:
            st.dataframe(rows, use_container_width=True, hide_index=True)
        else:
            st.info("暂无状态码分布。")


def render_experience_library_status_panel(cognition: dict[str, Any]) -> None:
    """Render experience-library status and live matching result."""
    symbol = str(cognition.get("symbol") or st.session_state.get("current_symbol", "")).upper()
    version_statuses = get_cached_experience_library_versions_status()
    version_options = list(EXPERIENCE_LIBRARY_VERSIONS.keys())
    current_version = get_selected_experience_library_version()
    if st.session_state.get("experience_library_version") not in version_options:
        st.session_state["experience_library_version"] = DEFAULT_EXPERIENCE_LIBRARY_VERSION
    if st.session_state.get("experience_mode") not in {"single", "fused"}:
        st.session_state["experience_mode"] = "single"
    selected_mode = st.radio(
        "经验库模式",
        ["single", "fused"],
        index=1 if get_selected_experience_mode() == "fused" else 0,
        format_func=lambda mode: "融合模式" if mode == "fused" else "单库模式",
        horizontal=True,
        key="experience_mode",
    )
    selected_version = st.selectbox(
        "经验库版本选择",
        version_options,
        index=version_options.index(current_version) if current_version in version_options else 0,
        format_func=lambda version: (
            f"{version}：{EXPERIENCE_LIBRARY_LABELS.get(version, version)}"
            f"（{'可用' if (version_statuses.get(version) or {}).get('available') else '不可用'}）"
        ),
        key="experience_library_version",
    )
    query = build_experience_query_from_cognition(symbol, cognition)
    summary = load_cached_experience_library_summary(selected_version)
    match = match_cached_experience(query, selected_version)
    market_cognition_state_code = str(cognition.get("state_code") or "")
    experience_query_state_code = str(query.get("experience_query_state_code") or query.get("state_code") or "")
    state_code_consistent = market_cognition_state_code == experience_query_state_code
    if not state_code_consistent:
        append_debug_log(
            EXPERIENCE_MATCH_LOG,
            f"state_code_mismatch symbol={symbol} cognition={market_cognition_state_code or '-'} query={experience_query_state_code or '-'}",
        )
        st.warning(f"经验查询 state_code 与市场认知不一致：市场认知 {market_cognition_state_code or '-'}，经验查询 {experience_query_state_code or '-'}")
    status = "可用" if summary.get("available") else "未接入" if not summary.get("manifest_found") else "格式不完整"
    status_class = "green" if summary.get("available") else "yellow" if summary.get("manifest_found") else "red"
    data_sources = str(summary.get("data_sources") or get_experience_library_data_sources(selected_version))
    version_rows = "".join(
        f"""<div class="summary-card">
          <div class="summary-label">{escape(version)} · {escape(EXPERIENCE_LIBRARY_LABELS.get(version, version))}</div>
          <div class="summary-value {'green' if (status_item or {}).get('available') else 'red'}">{'可用' if (status_item or {}).get('available') else '不可用'}</div>
          <div class="module-desc">{escape(str((status_item or {}).get('data_sources') or get_experience_library_data_sources(version)))}｜{escape(str((status_item or {}).get('path') or EXPERIENCE_LIBRARY_VERSIONS.get(version, '')))}</div>
        </div>"""
        for version, status_item in version_statuses.items()
    )
    state_summary = str(query.get("state_vector_summary") or "状态向量缺失")
    if len(state_summary) > 180:
        state_summary = state_summary[:180].rstrip() + "..."
    def _match_label(level: dict[str, Any], symbol_scope: bool = False) -> str:
        match_type = str(level.get("match_type") or "NONE").upper()
        if match_type == "EXACT_STATE_CODE":
            return "exact命中"
        if match_type == "EXPANDED_SIMILAR":
            return "精确+扩展命中"
        if match_type == "SIMILAR_STATE_CODE":
            return "相似状态命中"
        if match_type == "VECTOR_NEAREST":
            return "向量近邻命中"
        if symbol_scope:
            return "单币种无相似经验，使用同类币种与全市场经验"
        return "未命中"
    def _level_card(label: str, level: dict[str, Any]) -> str:
        hit = bool(level.get("matched"))
        sample = int(float(level.get("matched_sample_count") or 0))
        text = _match_label(level, label == "单币种匹配")
        color = "green" if hit else "yellow" if level.get("available") else "red"
        return f"""<div class="summary-card"><div class="summary-label">{escape(label)}</div><div class="summary-value {color}">{escape(text)}</div><div class="module-desc">样本 {sample}｜权重 {float(level.get("weight", 0) or 0) * 100:.1f}%｜相似度 {float(level.get("similarity", 0) or 0):.1f}</div></div>"""
    def _pct(value: Any) -> str:
        return format_pct_value(value, already_percent=True, digits=1)
    def _ret_pct(value: Any) -> str:
        return format_pct_value(value, digits=2, signed=True)
    def _plain(value: Any, digits: int = 1) -> str:
        number = safe_number(value)
        return "–" if number is None else f"{number:.{digits}f}"
    symbol_level = match.get("symbol_level") or {}
    group_level = match.get("group_level") or {}
    global_level = match.get("global_level") or {}
    comparison_matches = {
        version: match_cached_experience(query, version)
        for version in version_options
    }
    fused_result = build_cached_fused_experience_result(query) if selected_mode == "fused" else {}
    total_samples = int(float(match.get("matched_sample_count") or 0))
    exact_samples = int(float(match.get("exact_sample_count") or 0))
    similar_samples = int(float(match.get("similar_state_sample_count") or 0))
    vector_samples = int(float(match.get("vector_nearest_sample_count") or 0))
    avg_similarity = float(match.get("avg_similarity") or 0)
    used_match_layers_text = "、".join(str(x) for x in list(match.get("used_match_layers") or [])) or "无"
    expansion_note = str(match.get("match_expansion_note") or "")
    sample_level = str(match.get("sample_confidence_level") or "等待样本")
    participation_status = str(match.get("experience_participation_status") or ("弃权" if match.get("vote") == "ABSTAIN" else "谨慎参与"))
    abstain_reason = str(match.get("abstain_reason") or match.get("sample_confidence_note") or "")
    participation_color = "green" if participation_status == "参与投票" else "yellow" if participation_status in {"谨慎参与", "仅参考"} else "red"
    warnings_text = "；".join(str(item) for item in list(match.get("warnings") or [])[:4])
    detail_rows = "".join(
        f"""<div class="status-card" style="margin-top:8px;">
          <b>{escape(label)}</b><br>
          状态：{escape(_match_label(level, label == "单币种经验"))}｜
          样本：{int(float(level.get("matched_sample_count") or 0))}｜
          exact：{int(float(level.get("exact_sample_count") or 0))}｜
          相似：{int(float(level.get("similar_state_sample_count") or 0))}｜
          向量：{int(float(level.get("vector_nearest_sample_count") or 0))}｜
          匹配：{escape(str(level.get("match_type") or "-"))}｜
          命中状态码：{escape(str(level.get("matched_state_code") or "-"))}｜
          Layer：{escape(str(level.get("layer") or level.get("scope_type") or "-"))}｜
          Confidence：{float(level.get("confidence", 0) or 0):.1f}｜
          DataQuality：{float(level.get("data_quality", 0) or 0):.1f}<br>
          30m 上涨/震荡/下跌：{_pct(level.get("historical_30m_up_probability"))}/{_pct(level.get("historical_30m_sideways_probability"))}/{_pct(level.get("historical_30m_down_probability"))}｜
          MFE90/MAE90：{_ret_pct(level.get("mfe_p90"))}/{_ret_pct(level.get("mae_p90"))}<br>
          {escape(str(level.get("reason") or ""))}
        </div>"""
        for label, level in (("单币种经验", symbol_level), ("同类币种经验", group_level), ("全市场经验", global_level))
    )
    candidate_groups = [str(x) for x in list(query.get("symbol_group_candidates") or []) if x]
    primary_group = str(
        next(
            (item for item in [query.get("primary_group"), query.get("symbol_group"), *candidate_groups] if item and str(item).upper() != "UNKNOWN"),
            query.get("primary_group") or query.get("symbol_group") or "UNKNOWN",
        )
    )
    candidate_groups_text = ", ".join(candidate_groups) or "-"
    used_group = str(group_level.get("used_symbol_group") or primary_group or "-")
    consistency_text = "一致" if state_code_consistent else "不一致"
    consistency_color = "green" if state_code_consistent else "red"
    def _comparison_card(version: str, item: dict[str, Any]) -> str:
        item_status = item.get("experience_library_status") or version_statuses.get(version) or {}
        available = bool(item.get("available") and item_status.get("available"))
        notice = ""
        if version == "oi_longshort_recent30_v1":
            notice = "该经验库仅覆盖最近30天 OI / 多空比样本，不代表半年完整历史。"
        if not available:
            return f"""<div class="summary-card">
              <div class="summary-label">{escape(version)}</div>
              <div class="summary-value red">不可用</div>
              <div class="module-desc">{escape(str(item_status.get("message") or item.get("reason") or "经验库不可用。"))}</div>
              <div class="module-desc">{escape(str(item_status.get("path") or EXPERIENCE_LIBRARY_VERSIONS.get(version, "")))}</div>
              <div class="module-desc">{escape(notice)}</div>
            </div>"""
        return f"""<div class="summary-card">
          <div class="summary-label">{escape(version)}</div>
          <div class="summary-value {_signal_color(str(item.get("vote", "ABSTAIN")))}">{escape(str(item.get("vote", "ABSTAIN")))} / {escape(str(item.get("direction", "WAIT")))}</div>
          <div class="module-desc">30m上涨概率：{_pct(item.get("historical_30m_up_probability"))}</div>
          <div class="module-desc">60m上涨概率：{_pct(item.get("historical_60m_up_probability"))}</div>
          <div class="module-desc">建议止损：{_ret_pct(item.get("suggested_stop_loss"))}</div>
          <div class="module-desc">建议止盈1：{_ret_pct(item.get("suggested_take_profit_1"))}</div>
          <div class="module-desc">建议止盈2：{_ret_pct(item.get("suggested_take_profit_2"))}</div>
          <div class="module-desc">ExperienceConfidence：{_plain(item.get("confidence"))} / 100</div>
          <div class="module-desc">{escape(notice)}</div>
        </div>"""
    comparison_cards = "".join(_comparison_card(version, comparison_matches.get(version) or {}) for version in version_options)
    def _fusion_library_card(version: str, item: dict[str, Any], weight: Any) -> str:
        available = bool(item.get("available") and item.get("matched"))
        notice = "最近30天 OI / 多空比修正库，不是长期历史库。" if version == "oi_longshort_recent30_v1" else ""
        if not available:
            return f"""<div class="summary-card">
              <div class="summary-label">{escape(version)}</div>
              <div class="summary-value red">未使用</div>
              <div class="module-desc">权重：0.0%｜{escape(str(item.get("reason") or "不可用或未命中"))}</div>
              <div class="module-desc">{escape(notice)}</div>
            </div>"""
        return f"""<div class="summary-card">
          <div class="summary-label">{escape(version)}</div>
          <div class="summary-value {_signal_color(str(item.get("vote", "ABSTAIN")))}">{escape(str(item.get("vote", "ABSTAIN")))} / {escape(str(item.get("direction", "WAIT")))}</div>
          <div class="module-desc">权重：{_plain(weight)}%｜样本数：{int(float(item.get("matched_sample_count") or 0))}</div>
          <div class="module-desc">30m上涨概率：{_pct(item.get("historical_30m_up_probability"))}｜60m上涨概率：{_pct(item.get("historical_60m_up_probability"))}</div>
          <div class="module-desc">ExperienceConfidence：{_plain(item.get("confidence"))} / 100</div>
          <div class="module-desc">{escape(notice)}</div>
        </div>"""
    fused_library_results = fused_result.get("library_results") if isinstance(fused_result.get("library_results"), dict) else {}
    fused_weights = fused_result.get("library_weights") if isinstance(fused_result.get("library_weights"), dict) else {}
    fused_library_cards = "".join(
        _fusion_library_card(version, fused_library_results.get(version) or comparison_matches.get(version) or {}, fused_weights.get(version, 0))
        for version in version_options
    )
    fused_panel = ""
    if selected_mode == "fused":
        fused_panel = f"""
            <div class="status-card" style="margin-top:8px;">
              <b>多经验库融合结果</b><br>
              使用的经验库：{escape("、".join(str(x) for x in list(fused_result.get("used_libraries") or [])) or "无")}<br>
              权重：{escape(", ".join(f"{k}={v:.1f}%" for k, v in (fused_weights or {}).items()) or "无可用权重")}<br>
              明确提示：oi_longshort_recent30_v1 是最近30天修正库，不是长期历史库。
            </div>
            <div class="committee-grid" style="margin-top:8px;">
              {fused_library_cards}
            </div>
            <div class="committee-grid" style="margin-top:8px;">
              <div class="summary-card"><div class="summary-label">fused_vote</div><div class="summary-value {_signal_color(str(fused_result.get("fused_vote", "ABSTAIN")))}">{escape(str(fused_result.get("fused_vote", "ABSTAIN")))}</div><div class="module-desc">direction：{escape(str(fused_result.get("fused_direction", "WAIT")))}</div></div>
              <div class="summary-card"><div class="summary-label">fused_score</div><div class="summary-value blue">{_plain(fused_result.get("fused_score"))}</div></div>
              <div class="summary-card"><div class="summary-label">fused_confidence</div><div class="summary-value blue">{_plain(fused_result.get("fused_confidence"))} / 100</div></div>
              <div class="summary-card"><div class="summary-label">30m融合概率</div><div class="summary-value blue">涨{_pct(fused_result.get("historical_30m_up_probability"))}</div><div class="module-desc">震荡{_pct(fused_result.get("historical_30m_sideways_probability"))}｜跌{_pct(fused_result.get("historical_30m_down_probability"))}</div></div>
              <div class="summary-card"><div class="summary-label">60m融合概率</div><div class="summary-value blue">涨{_pct(fused_result.get("historical_60m_up_probability"))}</div><div class="module-desc">震荡{_pct(fused_result.get("historical_60m_sideways_probability"))}｜跌{_pct(fused_result.get("historical_60m_down_probability"))}</div></div>
              <div class="summary-card"><div class="summary-label">融合样本</div><div class="summary-value yellow">{int(float(fused_result.get("matched_sample_count") or 0))}</div><div class="module-desc">平均相似度：{_plain(fused_result.get("avg_similarity"))}</div></div>
              <div class="summary-card"><div class="summary-label">建议止损</div><div class="summary-value {_signal_color(str(fused_result.get("suggested_stop_loss", 0)))}">{_ret_pct(fused_result.get("suggested_stop_loss"))}</div></div>
              <div class="summary-card"><div class="summary-label">建议止盈1</div><div class="summary-value green">{_ret_pct(fused_result.get("suggested_take_profit_1"))}</div></div>
              <div class="summary-card"><div class="summary-label">建议止盈2</div><div class="summary-value green">{_ret_pct(fused_result.get("suggested_take_profit_2"))}</div></div>
            </div>
            <div class="status-card" style="margin-top:8px;">
              <b>融合 reason</b><br>
              {escape(str(fused_result.get("reason") or "融合经验暂无结论。"))}
            </div>
        """
    render_html(
        f"""
        <div class="app-shell">
          <div class="module-card">
            <div class="module-title">经验匹配结果</div>
            <div class="module-desc">经验委员按单币种、同类币种、全市场三层经验匹配当前市场认知。当前模式：{escape("融合模式" if selected_mode == "fused" else "单库模式")}；单库选择：{escape(selected_version)}。</div>
            <div class="committee-grid">
              {version_rows}
            </div>
            <div class="committee-grid">
              <div class="summary-card"><div class="summary-label">经验库状态</div><div class="summary-value {status_class}">{escape(status)}</div></div>
              <div class="summary-card"><div class="summary-label">当前经验库版本</div><div class="summary-value blue">{escape(selected_version)}</div><div class="module-desc">{escape(EXPERIENCE_LIBRARY_LABELS.get(selected_version, selected_version))}</div></div>
              <div class="summary-card"><div class="summary-label">data_sources</div><div class="summary-value yellow">{escape(data_sources)}</div></div>
              <div class="summary-card"><div class="summary-label">单币种经验</div><div class="summary-value {'green' if summary.get('symbol_level_found') else 'red'}">{'已发现' if summary.get('symbol_level_found') else '缺失'}</div></div>
              <div class="summary-card"><div class="summary-label">同类币种经验</div><div class="summary-value {'green' if summary.get('group_level_found') else 'red'}">{'已发现' if summary.get('group_level_found') else '缺失'}</div></div>
              <div class="summary-card"><div class="summary-label">全市场经验</div><div class="summary-value {'green' if summary.get('global_level_found') else 'red'}">{'已发现' if summary.get('global_level_found') else '缺失'}</div></div>
            </div>
            <div class="committee-grid" style="margin-top:8px;">
              {_level_card("单币种匹配", symbol_level)}
              {_level_card("同类币种匹配", group_level)}
              {_level_card("全市场匹配", global_level)}
              <div class="summary-card"><div class="summary-label">总样本</div><div class="summary-value blue">{total_samples}</div><div class="module-desc">单币种 {int(float(symbol_level.get("matched_sample_count") or 0))}｜同类 {int(float(group_level.get("matched_sample_count") or 0))}｜全市场 {int(float(global_level.get("matched_sample_count") or 0))}</div></div>
              <div class="summary-card"><div class="summary-label">精确状态样本</div><div class="summary-value blue">{exact_samples}</div><div class="module-desc">exact_state_code</div></div>
              <div class="summary-card"><div class="summary-label">相似状态样本</div><div class="summary-value yellow">{similar_samples}</div><div class="module-desc">similar_state_code，相似度≥70优先≥80</div></div>
              <div class="summary-card"><div class="summary-label">向量近邻样本</div><div class="summary-value yellow">{vector_samples}</div><div class="module-desc">vector_nearest，按状态向量加权距离</div></div>
              <div class="summary-card"><div class="summary-label">平均相似度</div><div class="summary-value {'green' if avg_similarity >= 80 else 'yellow' if avg_similarity >= 70 else 'red'}">{avg_similarity:.1f}</div><div class="module-desc">用于扩展经验置信度校准</div></div>
              <div class="summary-card"><div class="summary-label">经验置信度等级</div><div class="summary-value {participation_color}">{escape(sample_level)}</div><div class="module-desc">层级：{escape("、".join(str(x) for x in list(match.get("matched_layers") or [])) or "无")}</div></div>
              <div class="summary-card"><div class="summary-label">经验委员参与状态</div><div class="summary-value {participation_color}">{escape(participation_status)}</div><div class="module-desc">{escape(abstain_reason or "按历史经验置信度参与。")}</div></div>
              <div class="summary-card"><div class="summary-label">ExperienceConfidence</div><div class="summary-value blue">{_plain(match.get("confidence"))} / 100</div><div class="module-desc">历史经验置信度，受样本数与单币种命中影响</div></div>
              <div class="summary-card"><div class="summary-label">DataIntegrity</div><div class="summary-value yellow">{_plain(match.get("data_integrity_score"))} / 100</div><div class="module-desc">当前实时数据完整度，和历史置信度分开计算</div></div>
              <div class="summary-card"><div class="summary-label">state_code一致性</div><div class="summary-value {consistency_color}">{escape(consistency_text)}</div><div class="module-desc">市场认知 {escape(market_cognition_state_code or '-')}｜经验查询 {escape(experience_query_state_code or '-')}</div></div>
            </div>
            <div class="status-card" style="margin-top:8px;">
              <b>经验库路径</b><br>{escape(str(summary.get("path", EXPERIENCE_LIBRARY_VERSIONS.get(selected_version, ""))))}
            </div>
            <div class="status-card" style="margin-top:8px;">
              <b>当前查询输入</b><br>
              symbol：{escape(str(query.get("symbol") or "-"))}｜
              symbol_group：{escape(primary_group)}｜
              primary_group：{escape(primary_group)}｜
              fallback_groups：{escape(candidate_groups_text)}｜
              candidate_groups：{escape(candidate_groups_text)}<br>
              market_cognition_state_code：{escape(market_cognition_state_code or "-")}｜
              experience_query_state_code：{escape(experience_query_state_code or "-")}｜
              state_code一致性：{escape(consistency_text)}<br>
              同类匹配使用分组：{escape(used_group)}｜
              全市场匹配：{escape(str(global_level.get("match_type") or "NONE"))}<br>
              state_vector：{escape(state_summary)}
            </div>
            <div class="status-card" style="margin-top:8px;">
              <b>匹配扩展说明</b><br>
              精确状态样本：{exact_samples}｜
              相似状态样本：{similar_samples}｜
              向量近邻样本：{vector_samples}｜
              总参考样本：{total_samples}｜
              平均相似度：{avg_similarity:.1f}<br>
              使用层级：{escape(used_match_layers_text)}<br>
              {escape(expansion_note or "当前按经验库匹配策略生成历史参考。")}
            </div>
            <div class="committee-grid" style="margin-top:8px;">
              <div class="summary-card"><div class="summary-label">30分钟历史概率</div><div class="summary-value blue">涨{_pct(match.get("historical_30m_up_probability"))}</div><div class="module-desc">震荡{_pct(match.get("historical_30m_sideways_probability"))}｜跌{_pct(match.get("historical_30m_down_probability"))}</div></div>
              <div class="summary-card"><div class="summary-label">60分钟历史概率</div><div class="summary-value blue">涨{_pct(match.get("historical_60m_up_probability"))}</div><div class="module-desc">震荡{_pct(match.get("historical_60m_sideways_probability"))}｜跌{_pct(match.get("historical_60m_down_probability"))}</div></div>
              <div class="summary-card"><div class="summary-label">历史收益</div><div class="summary-value yellow">30m {_ret_pct(match.get("avg_return_30m"))}</div><div class="module-desc">60m {_ret_pct(match.get("avg_return_60m"))}｜中位30m {_ret_pct(match.get("median_return_30m"))}｜中位60m {_ret_pct(match.get("median_return_60m"))}</div></div>
              <div class="summary-card"><div class="summary-label">MFE / MAE</div><div class="summary-value yellow">MFE90 {_ret_pct(match.get("mfe_p90"))}</div><div class="module-desc">MFE50/75 {_ret_pct(match.get("mfe_p50"))}/{_ret_pct(match.get("mfe_p75"))}｜MAE50/75/90 {_ret_pct(match.get("mae_p50"))}/{_ret_pct(match.get("mae_p75"))}/{_ret_pct(match.get("mae_p90"))}</div></div>
              <div class="summary-card"><div class="summary-label">建议止损</div><div class="summary-value {_signal_color(str(match.get("suggested_stop_loss", 0)))}">{_ret_pct(match.get("suggested_stop_loss"))}</div></div>
              <div class="summary-card"><div class="summary-label">建议止盈1</div><div class="summary-value green">{_ret_pct(match.get("suggested_take_profit_1"))}</div></div>
              <div class="summary-card"><div class="summary-label">建议止盈2</div><div class="summary-value green">{_ret_pct(match.get("suggested_take_profit_2"))}</div><div class="module-desc">移动止损 {_ret_pct(match.get("suggested_trailing_stop"))}</div></div>
              <div class="summary-card"><div class="summary-label">经验委员投票</div><div class="summary-value {_signal_color(str(match.get("vote", "ABSTAIN")))}">{escape(str(match.get("vote", "ABSTAIN")))} / {escape(str(match.get("direction", "WAIT")))}</div><div class="module-desc">Score {_plain(match.get("score"))}｜ExperienceConfidence {_plain(match.get("confidence"))}｜DataIntegrity {_plain(match.get("data_integrity_score"))}</div></div>
            </div>
            <div class="status-card" style="margin-top:8px;">
              <b>三库对比</b><br>
              {escape("融合模式下，交易委员会经验委员使用 fused_experience_result 参与正式投票。" if selected_mode == "fused" else f"单库模式下，交易委员会经验委员使用用户当前选择的经验库版本：{selected_version}。")}
            </div>
            <div class="committee-grid" style="margin-top:8px;">
              {comparison_cards}
            </div>
            {fused_panel}
            <div class="status-card" style="margin-top:8px;">
              <b>经验委员理由</b><br>
              {escape(str(match.get("reason") or summary.get("message") or "经验库未接入，当前经验委员弃权。"))}
            </div>
            <details class="status-card" style="margin-top:8px;">
              <summary><b>详细层级</b></summary>
              {detail_rows}
              <div class="module-desc" style="margin-top:8px;">{escape(warnings_text)}</div>
            </details>
          </div>
        </div>
        """
    )


def render_market_cognition_panel(cognition: dict[str, Any] | None) -> None:
    """渲染 AI模型 9.2 市场认知区域。"""
    if not cognition:
        render_html(
            """
            <div class="app-shell">
              <div class="module-card">
                <div class="module-title">市场认知</div>
                <div class="status-card">市场认知量化等待数据。</div>
              </div>
            </div>
            """
        )
        return
    state_vector = cognition.get("state_vector") or {}
    path_30m = cognition.get("path_30m") or {}
    path_60m = cognition.get("path_60m") or {}
    missing = cognition.get("missing_fields") or []
    missing_text = "、".join(escape(str(item)) for item in missing) if missing else "无"
    status_rows = [
        ("趋势质量", cognition.get("trend_quality_score") or cognition.get("trend_score"), cognition.get("trend_state"), cognition.get("trend_direction")),
        ("资金", cognition.get("capital_score"), cognition.get("capital_state"), ""),
        ("结构", cognition.get("structure_score"), cognition.get("structure_state"), ""),
        ("行为", cognition.get("behavior_score"), cognition.get("behavior_state"), ""),
        ("风险", cognition.get("risk_score"), cognition.get("risk_state"), "越高越危险"),
        ("需求", cognition.get("demand_score"), cognition.get("demand_state"), cognition.get("demand_direction")),
    ]
    score_cards = []
    for label, score, state, extra in status_rows:
        value = f"{score if score is not None else '-'}"
        score_cards.append(
            f"""
            <div class="summary-card">
              <div class="summary-label">{escape(str(label))} {escape(str(state or '-'))}</div>
              <div class="summary-value {_signal_color(str(state or score))}">{escape(value)}</div>
              <div class="summary-label">{escape(str(extra or ''))}</div>
            </div>
            """
        )
    render_html(
        f"""
        <div class="app-shell">
          <div class="module-card">
            <div class="module-title">市场认知</div>
            <div class="metric-value {_signal_color(str(cognition.get("market_cognition_label", "")))}">
              {escape(str(cognition.get("state_code", "-")))} · {escape(str(cognition.get("market_cognition_score", "-")))} / 100
            </div>
            <div class="module-desc">{escape(str(cognition.get("cognition_summary", "等待市场认知量化。")))}</div>
            <div class="committee-grid">
              {''.join(score_cards)}
            </div>
            <div class="status-card" style="margin-top:8px;">
              <b>需求分析</b><br>
              买方需求：{escape(str(cognition.get("buy_demand_score", "-")))}｜
              卖方供给：{escape(str(cognition.get("sell_supply_score", "-")))}｜
              净需求：{escape(str(cognition.get("net_demand_score", "-")))}<br>
              紧迫度：{escape(str(cognition.get("urgency_score", "-")))}｜
              持续性：{escape(str(cognition.get("sustainability_score", "-")))}｜
              诱导风险：{escape(str(cognition.get("trap_risk_score", "-")))}<br>
              趋势方向：{escape(str(cognition.get("trend_direction", "-")))}｜
              趋势强度：{escape(str(cognition.get("trend_strength", "-")))}｜
              趋势质量：{escape(str(cognition.get("trend_quality_score", cognition.get("trend_score", "-"))))}<br>
              风险分：{escape(str(cognition.get("risk_score", "-")))}（越高越危险）｜
              风险安全分：{escape(str(cognition.get("risk_safe_score", "-")))}<br>
              {escape(str(cognition.get("demand_reason", "")))}
            </div>
            <div class="status-card" style="margin-top:8px;">
              <b>路径概率（{escape(str(cognition.get("probability_type", "rule_based_v1"))) }）</b><br>
              30分钟：上涨 {escape(str(path_30m.get("up", "-")))}%｜震荡 {escape(str(path_30m.get("sideways", "-")))}%｜下跌 {escape(str(path_30m.get("down", "-")))}%<br>
              60分钟：上涨 {escape(str(path_60m.get("up", "-")))}%｜震荡 {escape(str(path_60m.get("sideways", "-")))}%｜下跌 {escape(str(path_60m.get("down", "-")))}%
            </div>
            <div class="status-card" style="margin-top:8px;">
              <b>认知要点</b><br>
              主要矛盾：{escape(str(cognition.get("main_conflict", "-")))}<br>
              攻击点：{escape(str(cognition.get("attack_point", "-")))}<br>
              防守点：{escape(str(cognition.get("defense_point", "-")))}<br>
              失效点：{escape(str(cognition.get("failure_point", "-")))}<br>
              风险提示：{escape(str(cognition.get("risk_warning", "-")))}
            </div>
            <div class="status-card" style="margin-top:8px;">
              Confidence：{escape(str(cognition.get("confidence", "-")))}｜
              DataIntegrity：{escape(str(cognition.get("data_integrity_score", "-")))}｜
              缺失字段：{missing_text}
            </div>
          </div>
        </div>
        """
    )
    render_experience_library_status_panel(cognition)


def _to_weight_float(value: Any, default: float = 0.0) -> float:
    try:
        if value is None:
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


def _committee_member_weight(member_name: str, weights: dict[str, Any]) -> float:
    """按委员名称读取权重，兼容“大单/庄家委员”空格差异。"""
    if member_name in weights:
        return _to_weight_float(weights.get(member_name), 0)
    compact_name = member_name.replace(" ", "")
    for name, weight in weights.items():
        if str(name).replace(" ", "") == compact_name:
            return _to_weight_float(weight, 0)
    return 0.0


def _committee_vote_bucket(member: dict[str, Any]) -> str:
    name = str(member.get("member_name", ""))
    vote_code = str(member.get("vote_code") or "")
    if name in {"观察池委员", "策略验证委员"} or member.get("shadow") or member.get("member_type") == "shadow":
        return "影子复核"
    if member.get("veto") or vote_code == "veto":
        return "硬否决"
    if vote_code in {"strong_support", "support", "weak_support", "neutral_support"}:
        return "支持"
    if vote_code in {"weak_oppose", "oppose"}:
        return "反对"
    return "观望"


def _committee_weight_summary(decision: dict[str, Any]) -> dict[str, Any]:
    weights = dict(decision.get("committee_weights") or {})
    vote_detail = dict(decision.get("vote_detail") or {})
    summary: dict[str, Any] = {
        "support_weight": float(vote_detail.get("support_weight", 0) or 0),
        "oppose_weight": float(vote_detail.get("oppose_weight", 0) or 0),
        "neutral_weight": float(vote_detail.get("observe_weight", 0) or 0),
        "veto_weight": float(vote_detail.get("veto_weight", 0) or 0),
        "shadow_weight": float(vote_detail.get("shadow_weight", 0) or 0),
        "total_config_weight": sum(_to_weight_float(v, 0) for v in weights.values()),
        "direct_weight": float(vote_detail.get("formal_weight", 0) or 0),
        "rows": [],
    }
    for member in list(decision.get("member_votes") or []):
        name = str(member.get("member_name", "委员"))
        weight = _to_weight_float(member.get("weight"), _committee_member_weight(name, weights))
        bucket = _committee_vote_bucket(member)
        summary["rows"].append(
            {
                "name": name,
                "vote": str(member.get("vote", "建议观望")),
                "weight": weight,
                "bucket": bucket,
                "veto": bool(member.get("veto")),
                "confidence": member.get("confidence", 0),
                "member_type": str(member.get("member_type", "official")),
                "direction": str(member.get("direction_text", "中性")),
                "vote_strength": member.get("vote_strength", 0),
                "weighted_score": member.get("weighted_score", 0),
            }
        )
    return summary


def _fmt_weight(value: float) -> str:
    if abs(value - round(value)) < 0.01:
        return f"{int(round(value))}%"
    return f"{value:.1f}%"


def _render_score_breakdown(items: list[dict[str, Any]]) -> str:
    """渲染本地策略评分拆解。"""
    if not items:
        return '<div class="status-card">评分拆解暂不可用。</div>'
    rows = []
    for item in items:
        name = escape(str(item.get("name", "评分")))
        score = int(float(item.get("score", 0) or 0))
        level = escape(str(item.get("level", "等待数据")))
        explanation = escape(str(item.get("explanation", "等待数据同步。")))
        rows.append(
            f"""<div class="status-card" style="margin-top:6px;">
              <b>{name}</b>：<span class="{_signal_color(str(item.get("level", "")))}">{score} / 100 · {level}</span><br>
              {explanation}
            </div>"""
        )
    return "".join(rows)


def _format_signed_percent(value: Any) -> str:
    """格式化可为空的百分比变化。"""
    if value is None:
        return "待确认"
    try:
        number = float(value)
    except (TypeError, ValueError):
        return "待确认"
    return f"{number:+.2f}%"


def _format_funding(value: Any) -> str:
    """格式化 Funding 资金费率。"""
    if value is None:
        return "待确认"
    try:
        return f"{float(value) * 100:+.4f}%"
    except (TypeError, ValueError):
        return "待确认"


def _format_ratio(value: Any) -> str:
    """格式化多空比。"""
    if value is None:
        return "待确认"
    try:
        return f"{float(value):.2f}"
    except (TypeError, ValueError):
        return "待确认"


def _format_amount_short(value: Any) -> str:
    """格式化金额，适合手机端显示。"""
    try:
        number = float(value)
    except (TypeError, ValueError):
        return "待确认"
    sign = "-" if number < 0 else ""
    number = abs(number)
    if number >= 1_000_000_000:
        return f"{sign}{number / 1_000_000_000:.2f}B"
    if number >= 1_000_000:
        return f"{sign}{number / 1_000_000:.2f}M"
    if number >= 1_000:
        return f"{sign}{number / 1_000:.2f}K"
    return f"{sign}{number:.2f}"


def frontend_orderbook_html(symbol: str) -> str:
    """生成前端自刷新的盘口订单簿组件。"""
    return f"""
    <style>
      body {{ margin:0; background:transparent; font-family:Arial,'Microsoft YaHei',sans-serif; color:#E5E7EB; }}
      .orderbook-card {{ border:1px solid #1F2937; background:linear-gradient(180deg, rgba(15,23,42,.96), rgba(5,11,20,.92)); border-radius:14px; padding:10px; }}
      .orderbook-head {{ display:flex; justify-content:space-between; gap:8px; margin-bottom:6px; }}
      .orderbook-title {{ font-size:16px; font-weight:900; color:#fff; }}
      .desc,.status {{ color:#9CA3AF; font-size:11px; line-height:1.45; }}
      .status {{ text-align:right; }}
      .grid {{ display:grid; grid-template-columns:1fr; gap:8px; align-items:start; }}
      .book-panel {{ min-width:0; }}
      .summary-rail {{ min-width:0; border:1px solid rgba(31,41,55,.9); background:rgba(3,7,18,.34); border-radius:12px; padding:7px; }}
      @media (min-width:600px) {{
        .grid {{ grid-template-columns:minmax(0,1.62fr) minmax(170px,.9fr); }}
        .summary-rail {{ position:sticky; top:0; }}
      }}
      .row {{ position:relative; display:grid; grid-template-columns:1fr 1fr 1fr; align-items:center; min-height:22px; padding:1px 4px; border-bottom:1px solid rgba(51,65,85,.26); font-size:10px; overflow:hidden; }}
      .header {{ color:#9CA3AF; font-weight:800; background:rgba(15,23,42,.8); border-radius:7px; }}
      .bar {{ position:absolute; right:0; top:0; bottom:0; opacity:.22; z-index:0; }}
      .askbar {{ background:#F6465D; }} .bidbar {{ background:#00C087; }}
      .cell {{ position:relative; z-index:1; }} .right {{ text-align:right; }}
      .red {{ color:#F6465D; }} .green {{ color:#00C087; }} .yellow {{ color:#F0B90B; }} .blue {{ color:#3B82F6; }}
      .last {{ text-align:center; padding:8px; border-top:1px solid #1F2937; border-bottom:1px solid #1F2937; margin:4px 0; }}
      .last-price {{ color:#fff; font-size:20px; font-weight:900; }}
      .summary {{ display:grid; grid-template-columns:repeat(2,minmax(0,1fr)); gap:6px; margin-top:8px; }}
      .side-summary {{ display:grid; grid-template-columns:1fr; gap:6px; margin-top:7px; }}
      .box {{ min-height:42px; border:1px solid rgba(31,41,55,.95); background:linear-gradient(180deg, rgba(15,23,42,.82), rgba(8,13,24,.74)); border-radius:10px; padding:6px 8px; box-sizing:border-box; }}
      .side-summary .box {{ min-height:50px; display:flex; flex-direction:column; justify-content:center; }}
      .label {{ color:#9CA3AF; font-size:10px; font-weight:800; letter-spacing:0; }} .value {{ font-size:16px; font-weight:900; margin-top:3px; line-height:1.12; word-break:break-word; }}
      .side-summary .value {{ font-size:17px; }}
      .ratio {{ height:8px; display:flex; overflow:hidden; border-radius:999px; background:#111827; border:1px solid #1F2937; }}
      .buy {{ background:#00C087; }} .sell {{ background:#F6465D; }}
      .section-title {{ margin-top:10px; padding-top:8px; border-top:1px solid #1F2937; color:#fff; font-size:14px; font-weight:900; }}
      .whale-panel {{ margin-top:8px; border-top:1px solid #1F2937; padding-top:8px; }}
      .whale-row {{ display:grid; grid-template-columns:.72fr 1fr 1fr 1fr; gap:4px; min-height:22px; align-items:center; border-bottom:1px solid rgba(51,65,85,.26); font-size:10px; }}
      .whale-head {{ color:#9CA3AF; font-weight:800; background:rgba(15,23,42,.8); border-radius:7px; }}
      .whale-note {{ color:#9CA3AF; font-size:11px; line-height:1.45; margin-top:5px; }}
    </style>
    <div class="orderbook-card">
      <div class="orderbook-head">
        <div><div class="orderbook-title">{symbol} 盘口订单簿</div><div class="desc">Binance Public Depth｜买盘10档 / 卖盘10档</div></div>
        <div class="status" id="status">连接中...</div>
      </div>
      <div class="grid">
        <div class="book-panel">
          <div class="row header"><div>卖盘价格</div><div class="right">数量</div><div class="right">累计</div></div>
          <div id="asks"></div>
          <div class="last"><div class="last-price" id="lastPrice">正在获取</div><div id="change" class="yellow">--</div></div>
          <div class="row header"><div>买盘价格</div><div class="right">数量</div><div class="right">累计</div></div>
          <div id="bids"></div>
        </div>
        <div class="summary-rail">
          <div class="ratio"><div id="buyBar" class="buy" style="width:50%"></div><div id="sellBar" class="sell" style="width:50%"></div></div>
          <div class="side-summary">
            <div class="box"><div class="label">买盘</div><div class="value green" id="buyRatio">--</div></div>
            <div class="box"><div class="label">卖盘</div><div class="value red" id="sellRatio">--</div></div>
            <div class="box"><div class="label">盘口状态</div><div class="value yellow" id="obState">等待数据</div></div>
            <div class="box"><div class="label">多空倾向</div><div class="value blue" id="bias">等待数据</div></div>
            <div class="box"><div class="label">大买单</div><div class="value green" id="largeBid">--</div></div>
            <div class="box"><div class="label">大卖单</div><div class="value red" id="largeAsk">--</div></div>
          </div>
        </div>
      </div>
      <div class="whale-panel">
        <div class="section-title" style="margin-top:0;">大单监控 / 大资金行为面板</div>
        <div class="summary">
          <div class="box"><div class="label">大单强度</div><div class="value yellow" id="whaleScore">等待数据</div></div>
          <div class="box"><div class="label">大单方向</div><div class="value blue" id="whaleStatus">等待数据</div></div>
          <div class="box"><div class="label">庄家行为</div><div class="value blue" id="whaleDealer">等待数据</div></div>
          <div class="box"><div class="label">风险提示</div><div class="value yellow" id="whaleRisk">等待确认</div></div>
          <div class="box"><div class="label">5分钟净流入</div><div class="value" id="whaleNet">等待数据</div></div>
          <div class="box"><div class="label">15分钟净流入</div><div class="value" id="whaleNet15">等待数据</div></div>
          <div class="box"><div class="label">主动买入金额</div><div class="value green" id="whaleBuy">等待数据</div></div>
          <div class="box"><div class="label">主动卖出金额</div><div class="value red" id="whaleSell">等待数据</div></div>
          <div class="box"><div class="label">最大买单</div><div class="value green" id="whaleMaxBuy">--</div></div>
          <div class="box"><div class="label">最大卖单</div><div class="value red" id="whaleMaxSell">--</div></div>
          <div class="box"><div class="label">买入/卖出笔数</div><div class="value" id="whaleCounts">--</div></div>
          <div class="box"><div class="label">更新时间</div><div class="value" id="whaleTime">--</div></div>
        </div>
        <div class="whale-note" id="whaleConclusion">大单综合结论正在同步。</div>
        <div class="whale-note" id="whaleExplanation">大单数据正在同步。</div>
        <details class="whale-note" style="margin-top:8px;">
          <summary style="cursor:pointer;color:#E5E7EB;font-weight:800;">大单监控调试信息</summary>
          <div id="whaleDebug" style="margin-top:6px;line-height:1.55;">等待大单调试数据。</div>
        </details>
        <div style="margin-top:8px;">
          <div class="section-title" style="margin-top:0;">最新大单</div>
          <div class="whale-row whale-head"><div>时间</div><div>方向</div><div>价格</div><div>金额</div></div>
          <div id="whaleRows"><div class="whale-note">当前交易对象大单数据不足。</div></div>
        </div>
        <div class="whale-note" id="whaleStats">1分钟 / 5分钟 / 15分钟统计等待数据。</div>
      </div>
    </div>
    <script>
      const symbol = "{symbol}";
      {frontend_api_client_js("fetchOrderbookJson")}
      function clean(v) {{ return String(v).replace(/0+$/,'').replace(/\\.$/,''); }}
      function fmtPrice(v) {{
        const n = Number(v);
        if (!Number.isFinite(n)) return "-";
        if (n >= 1000) return n.toLocaleString(undefined, {{minimumFractionDigits:2, maximumFractionDigits:2}});
        if (n >= 1) return n.toLocaleString(undefined, {{minimumFractionDigits:4, maximumFractionDigits:4}});
        return n.toFixed(8).replace(/0+$/,'').replace(/\\.$/,'');
      }}
      function sum(levels) {{ return levels.reduce((a,l)=>a+Number(l[1]),0); }}
      function cumulative(levels) {{
        let total = 0;
        return levels.map(l => {{ total += Number(l[1]); return {{price:l[0], qty:l[1], cumulative:total}}; }});
      }}
      function renderRows(el, rows, side, maxQty) {{
        el.innerHTML = rows.map(r => {{
          const width = maxQty > 0 ? Math.min(100, Number(r.qty) / maxQty * 100) : 0;
          return `<div class="row"><div class="bar ${{side}}bar" style="width:${{width.toFixed(2)}}%"></div><div class="cell ${{side==='ask'?'red':'green'}}">${{clean(r.price)}}</div><div class="cell right">${{clean(r.qty)}}</div><div class="cell right">${{clean(r.cumulative.toFixed(8))}}</div></div>`;
        }}).join("");
      }}
      function fmtAmount(v) {{
        const n = Number(v || 0);
        const sign = n < 0 ? "-" : "";
        const a = Math.abs(n);
        if (a >= 1e9) return `${{sign}}${{(a/1e9).toFixed(2)}}B`;
        if (a >= 1e6) return `${{sign}}${{(a/1e6).toFixed(2)}}M`;
        if (a >= 1e3) return `${{sign}}${{(a/1e3).toFixed(2)}}K`;
        return `${{sign}}${{a.toFixed(2)}}`;
      }}
      function hasValue(v) {{ return v !== undefined && v !== null && v !== ""; }}
      function valueOr(v, fallback) {{ return hasValue(v) ? v : fallback; }}
      function num(v) {{ return Number(hasValue(v) ? v : 0); }}
      function orderText(order, emptyText) {{
        order = order || {{}};
        if (!hasValue(order.amount) && !hasValue(order.amount_text)) return emptyText;
        const priceText = valueOr(order.price_text, fmtPrice(order.price));
        const qtyText = valueOr(order.quantity_text, clean(num(order.quantity).toFixed(8)));
        const amountText = valueOr(order.amount_text, fmtAmount(order.amount));
        return `${{priceText}} / ${{qtyText}} / ${{amountText}}`;
      }}
      function updateWhales(whale) {{
        whale = whale || {{}};
        const stats = whale.stats || {{}};
        const s1 = stats["1m"] || {{}};
        const s5 = stats["5m"] || {{}};
        const s15 = stats["15m"] || {{}};
        const score = num(valueOr(whale.whale_score, whale.score));
        const level = valueOr(whale.whale_score_text, valueOr(whale.level, score === 0 ? "暂无大单" : "等待数据"));
        const direction = valueOr(whale.whale_direction, valueOr(whale.status, "等待数据"));
        const updated = valueOr(whale.updated_time, valueOr(whale.updated_at, "--"));
        const net = num(valueOr(whale.net_inflow_5m, s5.net_amount));
        const net15 = num(valueOr(whale.net_inflow_15m, s15.net_amount));
        const buyAmount = num(valueOr(whale.active_buy_amount, s5.buy_amount));
        const sellAmount = num(valueOr(whale.active_sell_amount, s5.sell_amount));
        const buyCount = num(valueOr(whale.buy_whale_count, s5.buy_count));
        const sellCount = num(valueOr(whale.sell_whale_count, s5.sell_count));
        document.getElementById("whaleScore").textContent = `${{score}} / 100 · ${{level}}`;
        document.getElementById("whaleStatus").textContent = direction;
        document.getElementById("whaleTime").textContent = updated;
        document.getElementById("whaleExplanation").textContent = valueOr(whale.explanation, "大单数据正在同步。");
        if (whale.error) {{
          document.getElementById("whaleStatus").textContent = "大单数据获取失败";
          document.getElementById("whaleRisk").textContent = "请稍后重试";
        }}
        if (!Object.keys(whale).length) {{
          document.getElementById("whaleStatus").textContent = "正在获取大单数据";
          document.getElementById("whaleRisk").textContent = "等待首次缓存";
        }}
        const netEl = document.getElementById("whaleNet");
        netEl.textContent = fmtAmount(net);
        netEl.className = net >= 0 ? "value green" : "value red";
        const net15El = document.getElementById("whaleNet15");
        net15El.textContent = fmtAmount(net15);
        net15El.className = net15 >= 0 ? "value green" : "value red";
        document.getElementById("whaleBuy").textContent = fmtAmount(buyAmount);
        document.getElementById("whaleSell").textContent = fmtAmount(sellAmount);
        document.getElementById("whaleCounts").textContent = valueOr(whale.buy_sell_count_text, `买入 ${{buyCount}} 笔 / 卖出 ${{sellCount}} 笔`);
        document.getElementById("whaleStats").textContent =
          `1分钟：大单${{num(s1.count)}}笔｜净流入 ${{fmtAmount(s1.net_amount)}}　5分钟：买入 ${{fmtAmount(buyAmount)}}｜卖出 ${{fmtAmount(sellAmount)}}｜净流入 ${{fmtAmount(net)}}　15分钟：大单${{num(s15.count)}}笔｜净流入 ${{fmtAmount(net15)}}`;
        const rows = ((whale.latest || []).length ? whale.latest : (whale.recent_trades || [])).slice(0,5);
        const showingRecentTrades = !(whale.latest || []).length && rows.length;
        const buyRows = (whale.latest || []).filter(t => t.direction === "主动买入");
        const sellRows = (whale.latest || []).filter(t => t.direction !== "主动买入");
        const maxBuy = buyRows.reduce((m,t)=>Number(t.amount || 0)>Number(m.amount||0)?t:m, {{}});
        const maxSell = sellRows.reduce((m,t)=>Number(t.amount || 0)>Number(m.amount||0)?t:m, {{}});
        document.getElementById("whaleMaxBuy").textContent = orderText(whale.largest_buy_order || maxBuy, "暂无大买单");
        document.getElementById("whaleMaxSell").textContent = orderText(whale.largest_sell_order || maxSell, "暂无大卖单");
        const dealerText = valueOr(whale.dealer_behavior, net15 > 0 && score >= 60 ? "疑似吸筹/拉升" : net15 < 0 && score >= 60 ? "疑似派发/压制" : "无明显行为");
        document.getElementById("whaleDealer").textContent = dealerText;
        const riskText = valueOr(whale.risk_tip, score >= 80 ? "活跃，防剧烈波动" : Math.abs(net15) > 0 ? "注意方向延续" : "当前暂无明显大单，但成交数据正常。");
        document.getElementById("whaleRisk").textContent = riskText;
        document.getElementById("whaleConclusion").textContent =
          net15 > 0 ? "大单综合结论：最近15分钟大单净流入偏正，短线资金更偏主动买入；若盘口买盘同步增强，偏向多头观察，但接近压力位时不建议追高。"
          : net15 < 0 ? "大单综合结论：最近15分钟大单净流出偏负，主动卖出压力更强；若盘口卖盘同步增强，需警惕下行延续。"
          : (Object.keys(whale).length ? "大单综合结论：当前暂无明显大单，但成交数据正常，资金行为暂未形成稳定方向。" : "大单综合结论：大单数据正在初始化。");
        document.getElementById("whaleRows").innerHTML = rows.length ? rows.map(t => {{
          const cls = t.direction === "主动买入" ? "green" : "red";
          return `<div class="whale-row"><div>${{t.time || "-"}}</div><div class="${{cls}}">${{t.direction || "-"}}</div><div>${{t.price_text || "-"}}</div><div>${{t.amount_text || "-"}}</div></div>`;
        }}).join("") : '<div class="whale-note">当前暂无达到阈值的大单，但成交数据正常。</div>';
        if (showingRecentTrades) {{
          document.getElementById("whaleRows").innerHTML =
            '<div class="whale-note">当前未触发大单阈值，以下显示最新成交。</div>' + document.getElementById("whaleRows").innerHTML;
        }}
        const debug = whale.debug || {{}};
        document.getElementById("whaleDebug").innerHTML =
          `当前交易对象：${{valueOr(debug.symbol, whale.symbol || symbol)}}<br>` +
          `数据源：${{valueOr(debug.data_source, "Binance Futures aggTrades")}}<br>` +
          `最近获取交易条数：${{num(valueOr(debug.raw_trade_count, whale.raw_trade_count))}}<br>` +
          `大单阈值：${{fmtAmount(valueOr(debug.threshold, whale.threshold))}} USDT<br>` +
          `5分钟统计交易数：${{num(debug.stats_5m_trade_count)}}<br>` +
          `15分钟统计交易数：${{num(debug.stats_15m_trade_count)}}<br>` +
          `主动买入金额：${{fmtAmount(valueOr(debug.active_buy_amount, buyAmount))}}<br>` +
          `主动卖出金额：${{fmtAmount(valueOr(debug.active_sell_amount, sellAmount))}}<br>` +
          `大单买入笔数：${{num(valueOr(debug.buy_whale_count, buyCount))}}<br>` +
          `大单卖出笔数：${{num(valueOr(debug.sell_whale_count, sellCount))}}<br>` +
          `数据质量：${{valueOr(debug.data_quality, whale.data_quality || "等待数据")}}<br>` +
          `错误信息：${{valueOr(debug.error, whale.error || "无")}}`;
      }}
      async function update() {{
        try {{
          const [depth, ticker, whale] = await Promise.all([
            fetchOrderbookJson(`/api/orderbook?symbol=${{encodeURIComponent(symbol)}}`),
            fetchOrderbookJson(`/api/ticker?symbol=${{encodeURIComponent(symbol)}}`),
            fetchOrderbookJson(`/api/whales?symbol=${{encodeURIComponent(symbol)}}`)
          ]);
          const bids = cumulative((depth.bids || []).map(l => [l.price_text ?? l.price, l.quantity_text ?? l.quantity]).slice(0,10));
          const asks = cumulative((depth.asks || []).map(l => [l.price_text ?? l.price, l.quantity_text ?? l.quantity]).slice(0,10));
          const maxQty = Math.max(...bids.concat(asks).map(r => Number(r.qty)), 0);
          renderRows(document.getElementById("asks"), asks.slice().reverse(), "ask", maxQty);
          renderRows(document.getElementById("bids"), bids, "bid", maxQty);
          const bidTotal = sum((depth.bids || []).map(l => [l.price_text ?? l.price, l.quantity_text ?? l.quantity]));
          const askTotal = sum((depth.asks || []).map(l => [l.price_text ?? l.price, l.quantity_text ?? l.quantity]));
          const total = bidTotal + askTotal;
          const buyRatio = total ? bidTotal / total * 100 : 50;
          const sellRatio = 100 - buyRatio;
          document.getElementById("buyBar").style.width = `${{buyRatio}}%`;
          document.getElementById("sellBar").style.width = `${{sellRatio}}%`;
          document.getElementById("buyRatio").textContent = `${{buyRatio.toFixed(1)}}%`;
          document.getElementById("sellRatio").textContent = `${{sellRatio.toFixed(1)}}%`;
          const hasDepth = bids.length > 0 && asks.length > 0;
          document.getElementById("obState").textContent = hasDepth ? (buyRatio > 58 ? "买盘强势" : sellRatio > 58 ? "卖盘强势" : "多空均衡") : "后台刷新中";
          document.getElementById("bias").textContent = hasDepth ? (buyRatio > sellRatio ? "多头占优" : sellRatio > buyRatio ? "空头占优" : "均衡") : "等待盘口";
          const largeBid = bids.reduce((m,r)=>Number(r.qty)>Number(m.qty||0)?r:m, {{}});
          const largeAsk = asks.reduce((m,r)=>Number(r.qty)>Number(m.qty||0)?r:m, {{}});
          document.getElementById("largeBid").textContent = largeBid.price ? `${{clean(largeBid.price)}} / ${{clean(largeBid.qty)}}` : "--";
          document.getElementById("largeAsk").textContent = largeAsk.price ? `${{clean(largeAsk.price)}} / ${{clean(largeAsk.qty)}}` : "--";
          const chg = Number(ticker.price_change_percent);
          document.getElementById("lastPrice").textContent = fmtPrice(ticker.last_price);
          const chgEl = document.getElementById("change");
          chgEl.textContent = Number.isFinite(chg) ? `${{chg > 0 ? "+" : ""}}${{chg.toFixed(2)}}%` : "正在获取";
          chgEl.className = Number.isFinite(chg) ? (chg >= 0 ? "green" : "red") : "yellow";
          document.getElementById("status").innerHTML = hasDepth ? `状态：实时<br>更新时间：${{new Date().toLocaleTimeString()}}<br>ID：${{depth.lastUpdateId || depth.last_update_id || "-"}}` : `状态：后台刷新中<br>${{depth.message || "等待盘口缓存"}}`;
          updateWhales(whale);
        }} catch (err) {{
          document.getElementById("status").innerHTML = "盘口数据获取失败<br>正在重试";
        }}
      }}
      update();
      setInterval(update, 1000);
    </script>
    """


def render_orderbook_system(symbol: str, ticker: dict[str, Any] | None) -> None:
    """渲染盘口订单簿系统。"""
    live_symbol = st.session_state.get("current_symbol", symbol)
    live_ticker = market_cache.get_ticker(live_symbol) or ticker
    if not live_ticker or live_refresh_due(f"ticker:{live_symbol}", 2.0):
        try:
            refresh_symbol_now(live_symbol)
            live_ticker = market_cache.get_ticker(live_symbol) or ticker
        except Exception as exc:
            market_cache.set_ticker_error(f"盘口价格刷新失败：{exc!r}")
    orderbook = market_cache.get_orderbook(live_symbol)
    if not orderbook or live_refresh_due(f"orderbook:{live_symbol}", 2.0):
        try:
            refresh_orderbook_now(live_symbol)
            orderbook = market_cache.get_orderbook(live_symbol)
        except Exception as exc:
            market_cache.set_orderbook_error(f"盘口服务端刷新失败：{exc!r}")
    whale = market_cache.get_whales(live_symbol)
    if not whale or live_refresh_due(f"whale:{live_symbol}", 5.0):
        try:
            refresh_whales_now(live_symbol)
            whale = market_cache.get_whales(live_symbol)
        except Exception as exc:
            market_cache.set_whale_error(f"大单服务端刷新失败：{exc!r}")
    snapshot = market_cache.snapshot()
    current_price = live_ticker.get("last_price") if live_ticker else None
    change = live_ticker.get("price_change_percent") if live_ticker else None
    analysis = analyze_orderbook(orderbook, current_price)
    bids = analysis.get("bids", [])
    asks = analysis.get("asks", [])
    limit = 10
    visible_asks = list(reversed(asks[:limit]))
    visible_bids = bids[:limit]
    orderbook_update_time = orderbook.get("updated_at") if orderbook else snapshot.get("orderbook_last_update_time", "初始化中")
    orderbook_refresh_count = snapshot.get("refresh_counts", {}).get("orderbook", 0)
    orderbook_id = orderbook.get("last_update_id", "-") if orderbook else "-"
    max_quantity = max([float(level.get("quantity", 0) or 0) for level in asks[:10] + bids[:10]] or [0])
    st.markdown(
        f"""<div class="app-shell"><div class="orderbook-card">
        <div class="orderbook-head">
          <div><div class="orderbook-title">{live_symbol} 盘口订单簿</div><div class="module-desc">Binance Public Depth｜买盘10档 / 卖盘10档</div></div>
          <div class="orderbook-status">状态：{snapshot.get("orderbook_status", "初始化中")}<br>更新时间：{orderbook_update_time}<br>刷新：{orderbook_refresh_count}｜ID：{orderbook_id}</div>
        </div>""",
        unsafe_allow_html=True,
    )
    if not orderbook:
        error = escape(str(snapshot.get("orderbook_last_error") or "正在获取盘口数据"))
        st.markdown(f'<div class="pending">{error}</div></div></div>', unsafe_allow_html=True)
    else:
        ask_rows = "".join(_orderbook_level_html(level, "ask", max_quantity, analysis.get("large_ask")) for level in visible_asks)
        bid_rows = "".join(_orderbook_level_html(level, "bid", max_quantity, analysis.get("large_bid")) for level in visible_bids)
        change_class = "green" if change is not None and change >= 0 else "red" if change is not None else "yellow"
        buy_ratio = float(analysis.get("buy_ratio", 0) or 0)
        sell_ratio = float(analysis.get("sell_ratio", 0) or 0)
        st.markdown(
            f"""<div class="orderbook-grid">
              <div class="orderbook-table">
                <div class="orderbook-row header"><div>卖盘价格</div><div class="ob-right">数量</div><div class="ob-right">累计</div></div>
                {ask_rows}
                <div class="last-price-box"><div class="last-price">{format_price(current_price)}</div><div class="{change_class}">{format_percent(change)}</div></div>
                <div class="orderbook-row header"><div>买盘价格</div><div class="ob-right">数量</div><div class="ob-right">累计</div></div>
                {bid_rows}
              </div>
              <div>
                <div class="ratio-bar"><div class="ratio-buy" style="width:{buy_ratio:.2f}%;"></div><div class="ratio-sell" style="width:{sell_ratio:.2f}%;"></div></div>
                <div class="orderbook-summary">
                  <div class="metric-box"><div class="metric-label">买盘</div><div class="metric-value green">{buy_ratio:.1f}%</div></div>
                  <div class="metric-box"><div class="metric-label">卖盘</div><div class="metric-value red">{sell_ratio:.1f}%</div></div>
                  <div class="metric-box"><div class="metric-label">盘口状态</div><div class="metric-value yellow">{analysis.get("status", "等待数据")}</div></div>
                  <div class="metric-box"><div class="metric-label">多空倾向</div><div class="metric-value blue">{analysis.get("bias", "等待数据")}</div></div>
                  <div class="metric-box"><div class="metric-label">支撑位</div><div class="metric-value green">{analysis.get("support_level") or "待确认"}</div></div>
                  <div class="metric-box"><div class="metric-label">压力位</div><div class="metric-value red">{analysis.get("resistance_level") or "待确认"}</div></div>
                </div>
                <div class="status-card" style="margin-top:8px;">
                  {_large_order_text("大买单", analysis.get("large_bid"))}<br>
                  {_large_order_text("大卖单", analysis.get("large_ask"))}
                </div>
              </div>
            </div></div></div>""",
            unsafe_allow_html=True,
        )
    whale = whale or {}
    whale_stats = whale.get("stats") or {}
    stats_5m = whale_stats.get("5m") or {}
    stats_15m = whale_stats.get("15m") or {}
    whale_score = whale.get("whale_score", whale.get("score", 0))
    whale_level = whale.get("whale_score_text") or whale.get("level") or ("暂无大单" if not whale else "等待数据")
    whale_direction = whale.get("whale_direction") or whale.get("status") or "等待数据"
    net_5m = whale.get("net_inflow_5m", stats_5m.get("net_amount", 0))
    net_15m = whale.get("net_inflow_15m", stats_15m.get("net_amount", 0))
    buy_amount = whale.get("active_buy_amount", stats_5m.get("buy_amount", 0))
    sell_amount = whale.get("active_sell_amount", stats_5m.get("sell_amount", 0))
    raw_trade_count = whale.get("raw_trade_count") or (whale.get("debug") or {}).get("raw_trade_count") or 0
    whale_update_time = whale.get("updated_time") or whale.get("updated_at") or snapshot.get("whale_last_update_time", "初始化中")
    whale_error = snapshot.get("whale_last_error") if not whale else whale.get("error")
    st.markdown(
        f"""<div class="app-shell"><div class="module-card">
        <div class="module-title">大单监控 / 大资金行为面板</div>
        <div class="module-desc">{escape(str(live_symbol))}｜服务端直读缓存｜成交源：{escape(str(whale.get("source", "Binance Futures aggTrades") if whale else "初始化中"))}</div>
        <div class="metric-grid">
          <div class="metric-box"><div class="metric-label">大单强度</div><div class="metric-value yellow">{escape(str(whale_score))} / 100 · {escape(str(whale_level))}</div></div>
          <div class="metric-box"><div class="metric-label">大单方向</div><div class="metric-value blue">{escape(str(whale_direction))}</div></div>
          <div class="metric-box"><div class="metric-label">庄家行为</div><div class="metric-value blue">{escape(str(whale.get("dealer_behavior", "无明显行为") if whale else "等待数据"))}</div></div>
          <div class="metric-box"><div class="metric-label">风险提示</div><div class="metric-value yellow">{escape(str(whale.get("risk_tip", "等待确认") if whale else "等待首次缓存"))}</div></div>
          <div class="metric-box"><div class="metric-label">5分钟净流入</div><div class="metric-value {_signal_color("资金流入" if float(net_5m or 0) >= 0 else "资金恐慌")}">{format_compact(net_5m)}</div></div>
          <div class="metric-box"><div class="metric-label">15分钟净流入</div><div class="metric-value {_signal_color("资金流入" if float(net_15m or 0) >= 0 else "资金恐慌")}">{format_compact(net_15m)}</div></div>
          <div class="metric-box"><div class="metric-label">主动买入金额</div><div class="metric-value green">{format_compact(buy_amount)}</div></div>
          <div class="metric-box"><div class="metric-label">主动卖出金额</div><div class="metric-value red">{format_compact(sell_amount)}</div></div>
          <div class="metric-box"><div class="metric-label">最大买单</div><div class="metric-value green">{escape(_whale_order_text(whale.get("largest_buy_order"), "暂无大买单"))}</div></div>
          <div class="metric-box"><div class="metric-label">最大卖单</div><div class="metric-value red">{escape(_whale_order_text(whale.get("largest_sell_order"), "暂无大卖单"))}</div></div>
          <div class="metric-box"><div class="metric-label">买入/卖出笔数</div><div class="metric-value">{escape(str(whale.get("buy_sell_count_text", "等待数据")))}</div></div>
          <div class="metric-box"><div class="metric-label">更新时间</div><div class="metric-value">{escape(str(whale_update_time))}</div></div>
        </div>
        <div class="status-card" style="margin-top:8px;">
          <b>大单综合结论</b><br>
          {escape(str(whale.get("explanation", "大单数据正在同步。" if not whale_error else whale_error)))}
        </div>
        <details class="status-card" style="margin-top:8px;" open>
          <summary><b>最新大单 / 最新成交</b></summary>
          <div style="margin-top:8px;">{_whale_trade_rows_html(whale)}</div>
        </details>
        <details class="status-card" style="margin-top:8px;">
          <summary><b>大单调试信息</b></summary>
          当前交易对象：{escape(str(live_symbol))}<br>
          原始成交条数：{escape(str(raw_trade_count))}<br>
          5分钟统计：买入 {format_compact(stats_5m.get("buy_amount", 0))} / 卖出 {format_compact(stats_5m.get("sell_amount", 0))} / 净流入 {format_compact(stats_5m.get("net_amount", 0))}<br>
          15分钟统计：净流入 {format_compact(stats_15m.get("net_amount", 0))}<br>
          错误信息：{escape(str(whale_error or "无"))}
        </details>
        </div></div>""",
        unsafe_allow_html=True,
    )


def _direction_text(direction: str) -> str:
    if direction == "long":
        return "偏多"
    if direction == "short":
        return "偏空"
    return "中性 / 观望"


def _permission_text(permission: str) -> str:
    if permission == "allowed":
        return "允许观察开仓"
    if permission == "cautious":
        return "谨慎轻仓"
    return "禁止开仓"


def render_local_strategy_decision(strategy: dict[str, Any]) -> None:
    """渲染统一本地策略决策模块。"""
    entry = strategy.get("entry_zone") or {}
    stop_loss = strategy.get("stop_loss") or {}
    tp1 = strategy.get("take_profit_1") or {}
    tp2 = strategy.get("take_profit_2") or {}
    data_quality = strategy.get("data_quality") or {}
    reasons_html = _render_numbered(list(strategy.get("reasons") or []))
    risks_html = _render_numbered(list(strategy.get("risks") or []))
    warnings = list(strategy.get("warnings") or [])
    warnings_html = _render_numbered(warnings) if warnings else "<li>暂无额外警告，但仍需严格控制仓位。</li>"
    quality_text = {"good": "良好", "partial": "部分缺失", "poor": "不足"}.get(str(data_quality.get("level")), "未知")
    missing = "、".join(data_quality.get("missing_fields") or []) or "无"
    direction = _direction_text(str(strategy.get("direction", "neutral")))
    permission = _permission_text(str(strategy.get("trade_permission", "blocked")))
    action = str(strategy.get("action", "观望"))
    vote_score = strategy.get("local_vote_score", 0)
    vote_grade = str(strategy.get("local_vote_grade", "D"))
    vote_decision = str(strategy.get("local_vote_decision", "只观察"))
    vote_reason = str(strategy.get("local_vote_reason", "等待策略数据同步。"))
    sections = strategy.get("analysis_sections") or {}
    long_reasons_html = _render_numbered(list(sections.get("long_reasons") or []))
    short_reasons_html = _render_numbered(list(sections.get("short_reasons") or []))
    current_risks_html = _render_numbered(list(sections.get("current_risks") or []))
    conflicts_html = _render_numbered(list(sections.get("signal_conflicts") or []))
    blocked_html = _render_numbered(list(sections.get("blocked_reasons") or []))
    score_breakdown_html = _render_score_breakdown(list(strategy.get("score_breakdown") or []))
    data_handling = str(strategy.get("data_quality_handling", "当前数据质量暂未确认，策略采用保守判断。"))
    st.markdown(
        f"""
        <div class="app-shell">
          <div class="module-card">
            <div class="module-title">本地策略决策</div>
            <div class="metric-value {_signal_color(action)}">{escape(action)} · {escape(direction)}</div>
            <div class="module-desc">统一读取K线、盘口、衍生品、清算、大单、庄家行为和风险雷达，不依赖外部AI。</div>
            <div class="status-card" style="margin-top:8px;">
              <b>第一层：本地策略核心结论</b><br>
              当前交易对象：{escape(str(strategy.get("symbol", "-")))}<br>
              本地策略方向：{escape(direction)}｜策略类型：{escape(str(strategy.get("strategy_name", "无有效策略")))}<br>
              操作建议：{escape(action)}｜交易权限：{escape(permission)}｜建议仓位：{escape(str(strategy.get("position_suggestion", "0%")))}<br>
              置信度：{strategy.get("confidence", 0)} / 100｜风险评分：{strategy.get("risk_score", 0)} / 100｜机会评分：{strategy.get("opportunity_score", 0)} / 100<br>
              本地投票：{vote_score} / 100 · {escape(vote_grade)}级｜{escape(vote_decision)}
            </div>
            <div class="metric-grid">
              <div class="metric-box"><div class="metric-label">本地投票分</div><div class="metric-value {_signal_color(vote_decision)}">{vote_score} / 100</div></div>
              <div class="metric-box"><div class="metric-label">投票评级</div><div class="metric-value {_signal_color(vote_decision)}">{escape(vote_grade)}级</div></div>
              <div class="metric-box"><div class="metric-label">投票决议</div><div class="metric-value {_signal_color(vote_decision)}">{escape(vote_decision)}</div></div>
              <div class="metric-box"><div class="metric-label">策略类型</div><div class="metric-value yellow">{escape(str(strategy.get("strategy_name", "无有效策略")))}</div></div>
              <div class="metric-box"><div class="metric-label">交易权限</div><div class="metric-value {_signal_color(permission)}">{escape(permission)}</div></div>
              <div class="metric-box"><div class="metric-label">置信度</div><div class="metric-value blue">{strategy.get("confidence", 0)} / 100</div></div>
              <div class="metric-box"><div class="metric-label">风险评分</div><div class="metric-value {_signal_color(str(strategy.get("risk_score", 0)))}">{strategy.get("risk_score", 0)} / 100</div></div>
              <div class="metric-box"><div class="metric-label">机会评分</div><div class="metric-value green">{strategy.get("opportunity_score", 0)} / 100</div></div>
              <div class="metric-box"><div class="metric-label">建议仓位</div><div class="metric-value yellow">{escape(str(strategy.get("position_suggestion", "0%")))}</div></div>
            </div>
            <div class="status-card" style="margin-top:8px;">
              <b>第二层：入场 / 止损 / 止盈计划</b><br>
              主分析周期：{escape(str(strategy.get("primary_timeframe", "-")))}｜大方向：{escape(str(strategy.get("higher_timeframe_bias", "中性")))}<br>
              参考入场：{escape(str(entry.get("text", "当前不适合开仓")))}<br>
              止损：{escape(_fmt_strategy_price(stop_loss.get("price")))}｜{escape(str(stop_loss.get("reason", "无有效入场，不设置止损")))}<br>
              止盈1：{escape(_fmt_strategy_price(tp1.get("price")))}｜{escape(str(tp1.get("reason", "无有效入场，不设置止盈")))}<br>
              止盈2：{escape(_fmt_strategy_price(tp2.get("price")))}｜{escape(str(tp2.get("reason", "无有效入场，不设置止盈")))}<br>
              风险收益比：{escape(str(strategy.get("risk_reward_ratio") or "待确认"))}<br>
              信号失效：{escape(str(strategy.get("invalid_condition", "等待结构确认")))}<br>
              本地委员投票理由：{escape(vote_reason)}
            </div>
            <details class="status-card" style="margin-top:8px;" open>
              <summary><b>第三层：原因与风险解释</b></summary>
              <div class="module-grid" style="margin-top:8px;">
                <div class="status-card"><b>看多原因</b><ol style="padding-left:18px;margin:6px 0 0 0;">{long_reasons_html}</ol></div>
                <div class="status-card"><b>看空原因</b><ol style="padding-left:18px;margin:6px 0 0 0;">{short_reasons_html}</ol></div>
                <div class="status-card"><b>当前风险</b><ol style="padding-left:18px;margin:6px 0 0 0;">{current_risks_html}</ol></div>
                <div class="status-card"><b>信号冲突</b><ol style="padding-left:18px;margin:6px 0 0 0;">{conflicts_html}</ol></div>
                <div class="status-card"><b>禁止开仓原因</b><ol style="padding-left:18px;margin:6px 0 0 0;">{blocked_html}</ol></div>
              </div>
            </details>
            <details class="status-card" style="margin-top:8px;">
              <summary><b>第四层：评分拆解</b></summary>
              {score_breakdown_html}
            </details>
            <details class="status-card" style="margin-top:8px;">
              <summary><b>第五层：数据质量与调试信息</b></summary>
              数据质量：{escape(str(data_quality.get("level", "poor")))}（{escape(quality_text)}）<br>
              缺失字段：{escape(missing)}<br>
              保守处理：{escape(data_handling)}<br>
              原始看多/看空理由：<ol style="padding-left:18px;margin:6px 0 0 0;">{reasons_html}</ol>
              原始风险：<ol style="padding-left:18px;margin:6px 0 0 0;">{risks_html}</ol>
              风控警告：<ol style="padding-left:18px;margin:6px 0 0 0;">{warnings_html}</ol>
            </details>
          </div>
        </div>
        """,
        unsafe_allow_html=True,
    )


def _committee_permission_text(permission: str) -> str:
    mapping = {
        "approved": "通过",
        "cautious": "谨慎通过",
        "rejected": "未通过",
        "blocked": "禁止开仓",
        "candidate": "顺势候选",
        "simulation_or_approval": "模拟/自动候选",
        "watch_candidate": "观察候选",
        "observe_only": "只观察",
        "no_auto_trade": "禁止自动交易",
    }
    return mapping.get(permission, permission or "等待决议")


def _committee_brief_summary(decision: dict[str, Any]) -> str:
    """Compact summary for signal/overview bars."""
    v91 = decision.get("trading_committee_v91") or {}
    risk = v91.get("risk_judge") or {}
    action = str(decision.get("final_action") or v91.get("final_action") or "等待决议")
    direction = str(decision.get("final_direction_text") or v91.get("final_direction") or "中性")
    confidence = decision.get("committee_confidence", v91.get("final_confidence", 0))
    risk_score = decision.get("committee_risk_score", risk.get("risk_score", 0))
    risk_verdict = str(risk.get("risk_verdict") or ("BLOCK" if decision.get("veto_members") else "PASS"))
    permission = _committee_permission_text(str(decision.get("trade_permission", "")))
    veto_members = list(decision.get("veto_members") or [])
    veto_text = f"｜否决：{'、'.join(str(x) for x in veto_members[:3])}" if veto_members else ""
    return (
        f"{action}｜方向：{direction}｜许可：{permission}｜"
        f"置信度：{confidence}/100｜风险：{risk_score}/100｜风险裁判：{risk_verdict}{veto_text}"
    )


def _committee_summary_fields(decision: dict[str, Any]) -> list[tuple[str, str, str]]:
    v91 = decision.get("trading_committee_v91") or {}
    risk = v91.get("risk_judge") or {}
    action = str(decision.get("final_action") or v91.get("final_action") or "等待决议")
    direction = str(decision.get("final_direction_text") or v91.get("final_direction") or "中性")
    permission = _committee_permission_text(str(decision.get("trade_permission", "")))
    confidence = str(decision.get("committee_confidence", v91.get("final_confidence", 0)))
    risk_score = str(decision.get("committee_risk_score", risk.get("risk_score", 0)))
    risk_verdict = str(risk.get("risk_verdict") or ("BLOCK" if decision.get("veto_members") else "PASS"))
    veto_members = list(decision.get("veto_members") or [])
    veto_text = "、".join(str(x) for x in veto_members[:2]) if veto_members else "未触发"
    return [
        ("结论", action, _signal_color(action)),
        ("方向", direction, _signal_color(direction)),
        ("许可", permission, _signal_color(action)),
        ("置信度", f"{confidence}/100", "blue"),
        ("风险", f"{risk_score}/100", _signal_color("禁止开仓" if risk_verdict == "BLOCK" else str(risk_score))),
        ("风险裁判", risk_verdict, _signal_color("禁止开仓" if risk_verdict == "BLOCK" else "支持交易")),
        ("否决", veto_text, _signal_color("禁止开仓" if veto_members else "支持交易")),
    ]


def _committee_full_summary_rows(decision: dict[str, Any]) -> list[tuple[str, str]]:
    weight_summary = _committee_weight_summary(decision)
    v91 = decision.get("trading_committee_v91") or {}
    risk = v91.get("risk_judge") or {}
    position = v91.get("position_plan") or {}
    execution = v91.get("execution_plan") or {}
    external_ai = decision.get("external_ai") or {}
    deepseek = external_ai.get("deepseek") or {}
    gemini = external_ai.get("gemini") or {}
    return [
        ("正式权重", f"支持{_fmt_weight(weight_summary['support_weight'])} / 观望{_fmt_weight(weight_summary['neutral_weight'])} / 反对{_fmt_weight(weight_summary['oppose_weight'] + weight_summary['veto_weight'])}"),
        ("共振等级", str(decision.get("resonance_text", "无共振"))),
        ("最终动作", str(decision.get("final_action", "等待决议"))),
        ("交易许可", _committee_permission_text(str(decision.get("trade_permission", "")))),
        ("风险裁判", f"{risk.get('risk_verdict', 'WAIT')} / 风险{risk.get('risk_score', decision.get('committee_risk_score', 0))}/100"),
        ("否决来源", "、".join(str(x) for x in list(decision.get("veto_members") or [])) or "未触发"),
        ("仓位委员会", f"{position.get('position_size_pct', 0)}% / {position.get('leverage', 1)}x"),
        ("执行委员会", str(execution.get("execution_type", "WAIT"))),
        ("DeepSeek", f"{deepseek.get('status', '等待')} / {deepseek.get('vote', '观望')} / 风险{deepseek.get('risk_level', '中')}"),
        ("Gemini", f"{gemini.get('status', '等待')} / {gemini.get('vote', '观望')} / 风险{gemini.get('risk_level', '中')}"),
        ("下一步", str((decision.get("explanation") or {}).get("next_condition") or decision.get("invalid_condition") or "等待下一轮数据确认")),
    ]


def _render_committee_summary_panel(decision: dict[str, Any]) -> str:
    cards = "".join(
        f"""<div class="committee-summary-item">
          <div class="label">{escape(label)}</div>
          <div class="value {color}" title="{escape(str(value))}">{escape(str(value))}</div>
        </div>"""
        for label, value, color in _committee_summary_fields(decision)
    )
    return dedent(f"""
      <div class="committee-summary-panel">
        <div class="committee-summary-title">交易委员会总结</div>
        <div class="committee-summary-strip">{cards}</div>
      </div>
    """)


def render_committee_full_summary_expander(decision: dict[str, Any], label: str = "查看完整委员会总结") -> None:
    with st.expander(label, expanded=False):
        for row_label, value in _committee_full_summary_rows(decision):
            st.markdown(f"**{row_label}**：{_safe_committee_text(value, 220)}")


def _render_trading_committee_v91(decision: dict[str, Any]) -> str:
    """Render the AI_MODEL 9.1 committee structure while keeping legacy fields."""
    v91 = decision.get("trading_committee_v91") or {}
    if not v91:
        return '<div class="status-card">9.1交易委员会结构等待下一轮决议生成。</div>'
    risk = v91.get("risk_judge") or {}
    position = v91.get("position_plan") or {}
    execution = v91.get("execution_plan") or {}
    members_html = "".join(
        f"""<div class="summary-card">
          <div class="summary-label">{escape(str(member.get("name", "委员")))} · {escape(str(member.get("role", "-")))}</div>
          <div class="summary-value {_signal_color(str(member.get("vote", "")))}">{escape(str(member.get("vote", "ABSTAIN")))} / {escape(str(member.get("direction", "WAIT")))}</div>
          <div class="module-desc">评分：{float(member.get("score", 0) or 0):.1f}｜Confidence：{float(member.get("confidence", 0) or 0):.1f}｜DataIntegrity：{float(member.get("data_integrity_score", 0) or 0):.1f}</div>
          <div class="module-desc">{escape(_safe_committee_text(member.get("reason", "")))}</div>
        </div>"""
        for member in list(v91.get("members") or [])
    ) or '<div class="status-card">暂无9.1委员结果。</div>'
    shadow_count = len(list(v91.get("shadow_members") or []))
    return dedent(f"""
    <details class="status-card" style="margin-top:8px;" open>
      <summary><b>AI模型 9.1 交易委员会结构</b></summary>
      <div class="committee-grid" style="margin-top:8px;">
        <div class="summary-card"><div class="summary-label">最终结论</div><div class="summary-value {_signal_color(str(v91.get("final_action", "WAIT")))}">{escape(str(v91.get("final_action", "WAIT")))} / {escape(str(v91.get("final_direction", "WAIT")))}</div></div>
        <div class="summary-card"><div class="summary-label">交易价值评分</div><div class="summary-value blue">{float(v91.get("trade_value_score", 0) or 0):.1f} / 100</div></div>
        <div class="summary-card"><div class="summary-label">最终置信度</div><div class="summary-value blue">{float(v91.get("final_confidence", 0) or 0):.1f} / 100</div></div>
        <div class="summary-card"><div class="summary-label">数据完整度</div><div class="summary-value yellow">{float(v91.get("final_data_integrity_score", 0) or 0):.1f} / 100</div></div>
        <div class="summary-card"><div class="summary-label">风险裁判</div><div class="summary-value {_signal_color("禁止开仓" if risk.get("blocked") else "支持交易")}">{escape(str(risk.get("risk_verdict", "WAIT")))}</div></div>
        <div class="summary-card"><div class="summary-label">风险评分</div><div class="summary-value yellow">{float(risk.get("risk_score", 0) or 0):.1f} / 100</div></div>
        <div class="summary-card"><div class="summary-label">仓位委员会</div><div class="summary-value {_signal_color("支持交易" if position.get("allow_position") else "反对交易")}">{float(position.get("position_size_pct", 0) or 0):.2f}% / {int(position.get("leverage", 1) or 1)}x</div></div>
        <div class="summary-card"><div class="summary-label">执行委员会</div><div class="summary-value {_signal_color("支持交易" if execution.get("execution_allowed") else "反对交易")}">{escape(str(execution.get("execution_type", "WAIT")))}</div></div>
        <div class="summary-card"><div class="summary-label">影子委员</div><div class="summary-value blue">{shadow_count} 个，仅参考</div></div>
      </div>
      <div class="status-card" style="margin-top:8px;"><b>风险裁判理由</b><br>{escape(_safe_committee_text(risk.get("block_reason") or "未触发阻断。"))}<br>{escape(_safe_committee_text("；".join(str(x) for x in list(risk.get("warnings") or [])[:4])))}</div>
      <div class="status-card" style="margin-top:8px;"><b>仓位委员会</b><br>{escape(_safe_committee_text(position.get("reason", "等待仓位建议。")))}</div>
      <div class="status-card" style="margin-top:8px;"><b>执行委员会</b><br>{escape(_safe_committee_text(execution.get("reason", "等待执行计划。")))}</div>
      <div class="committee-vote-grid" style="margin-top:8px;">{members_html}</div>
    </details>
    """)


def render_ai_committee_decision(decision: dict[str, Any]) -> None:
    """渲染交易委员会最终决议与委员投票。"""
    if not decision:
        st.warning("交易委员会暂时无法完成完整分析，已等待下一轮数据。")
        return
    permission = str(decision.get("trade_permission", "rejected"))
    action = str(decision.get("final_action", "继续观察"))
    direction = str(decision.get("final_direction_text", "中性"))
    simulation_text = "是" if decision.get("approved_for_simulation") else "否"
    supporting = list(decision.get("supporting_members") or [])
    opposing = list(decision.get("opposing_members") or [])
    veto_members = list(decision.get("veto_members") or [])
    reasons_html = _render_numbered(list(decision.get("main_reasons") or []))
    risks_html = _render_numbered(list(decision.get("main_risks") or []))
    warnings_html = _render_numbered(list(decision.get("final_warnings") or []))
    explanation = decision.get("explanation") or {}
    hard = decision.get("hard_veto_status") or {}
    soft = decision.get("soft_veto_status") or {}
    external_ai = decision.get("external_ai") or {}
    weights = decision.get("committee_weights") or {}
    weight_summary = _committee_weight_summary(decision)
    weight_vote_text = (
        f"支持{_fmt_weight(weight_summary['support_weight'])} / "
        f"观望{_fmt_weight(weight_summary['neutral_weight'])} / "
        f"反对{_fmt_weight(weight_summary['oppose_weight'] + weight_summary['veto_weight'])} / "
        f"影子{_fmt_weight(weight_summary['shadow_weight'])}"
    )
    weight_rows_html = "".join(
        f"""<div class="summary-card">
          <div class="summary-label">{escape(str(row["name"]))} · {escape("影子" if row.get("member_type") == "shadow" else "正式")}</div>
          <div class="summary-value {_signal_color(str(row["vote"]))}">{escape(str(row["vote"]))}</div>
          <div class="module-desc">权重：{_fmt_weight(float(row["weight"]))}｜计入：{escape(str(row["bucket"]))}｜方向：{escape(str(row.get("direction", "中性")))}｜信心：{escape(str(row["confidence"]))}</div>
          <div class="module-desc">vote_strength：{escape(str(row.get("vote_strength", 0)))}｜weighted_score：{escape(str(row.get("weighted_score", 0)))}</div>
        </div>"""
        for row in list(weight_summary.get("rows") or [])
    ) or '<div class="status-card">暂无委员权重明细。</div>'
    weight_text = " / ".join(f"{k}:{v}%" for k, v in weights.items()) if weights else "等待权重配置"
    deepseek = external_ai.get("deepseek") or {}
    gemini = external_ai.get("gemini") or {}
    ai_consensus = external_ai.get("external_ai_consensus") or {}
    deepseek_summary = deepseek.get("summary") or deepseek.get("main_opinion") or "DeepSeek影子委员未返回意见。"
    gemini_summary = gemini.get("summary") or gemini.get("chart_observation") or "Gemini影子委员未返回意见。"
    deepseek_reasons = _render_numbered(list(deepseek.get("reasons") or []))
    deepseek_risks = _render_numbered(list(deepseek.get("risks") or []))
    gemini_reasons = _render_numbered(list(gemini.get("reasons") or []))
    gemini_risks = _render_numbered(list(gemini.get("risks") or []))
    soft_text = "已触发：" + "、".join(soft.get("members") or []) if soft.get("triggered") else "未触发"
    veto_html = "当前无委员触发强制否决。"
    veto_class = "green"
    veto_status = "未触发"
    if veto_members:
        veto_status = "已触发"
        veto_html = "风险否决已触发，委员会禁止开仓。否决委员：" + "、".join(escape(str(name)) for name in veto_members)
        veto_class = "red"
    render_html(
        f"""
        <div class="app-shell">
          <div class="module-card">
            <div class="module-title">交易委员会最终决议</div>
            <div class="metric-value {_signal_color(action)}">{escape(action)} · {escape(direction)}</div>
            <div class="module-desc">委员会读取本地策略、趋势、资金、盘口、清算、大单、风险雷达和观察池状态，只生成决议和模拟候选，不执行交易。</div>
            <div class="committee-grid">
              <div class="summary-card"><div class="summary-label">当前交易对象</div><div class="summary-value yellow">{escape(str(decision.get("symbol", "-")))}</div></div>
              <div class="summary-card"><div class="summary-label">最终动作 / 方向</div><div class="summary-value {_signal_color(action)}">{escape(action)} / {escape(direction)}</div></div>
              <div class="summary-card"><div class="summary-label">交易许可</div><div class="summary-value {_signal_color(action)}">{escape(_committee_permission_text(permission))}</div></div>
              <div class="summary-card"><div class="summary-label">共振等级</div><div class="summary-value yellow">{escape(str(decision.get("resonance_text", "无共振")))}</div></div>
              <div class="summary-card"><div class="summary-label">委员会置信度</div><div class="summary-value blue">{decision.get("committee_confidence", 0)} / 100</div></div>
              <div class="summary-card"><div class="summary-label">委员会风险</div><div class="summary-value {_signal_color(str(decision.get("committee_risk_score", 0)))}">{decision.get("committee_risk_score", 0)} / 100</div></div>
              <div class="summary-card"><div class="summary-label">建议仓位</div><div class="summary-value yellow">{escape(str(decision.get("position_suggestion", "0%")))}</div></div>
              <div class="summary-card"><div class="summary-label">风控最大仓位</div><div class="summary-value yellow">{escape(str(decision.get("risk_max_position", "0%")))}</div></div>
              <div class="summary-card"><div class="summary-label">模拟候选</div><div class="summary-value {_signal_color("支持交易" if decision.get("approved_for_simulation") else "反对交易")}">{simulation_text}</div></div>
              <div class="summary-card"><div class="summary-label">风险否决</div><div class="summary-value {veto_class}">{veto_status}</div></div>
              <div class="summary-card"><div class="summary-label">投票统计</div><div class="summary-value">支持{len(supporting)} / 反对{len(opposing)} / 否决{len(veto_members)}</div></div>
              <div class="summary-card"><div class="summary-label">正式权重统计</div><div class="summary-value yellow">{weight_vote_text}</div></div>
            </div>
            {_render_trading_committee_v91(decision)}
            {_render_committee_summary_panel(decision)}
            <div class="status-card {veto_class}" style="margin-top:8px;">
              <b>风险否决状态</b><br>{veto_html}
            </div>
            <div class="status-card" style="margin-top:8px;">
              <b>决策权治理</b><br>
              本地策略委员定位：基础提案层。风险委员和实盘安全委员拥有硬否决权。DeepSeek/Gemini 当前为正式投票成员；观察池/策略验证暂为影子复核。<br>
              硬否决：{escape("已触发" if hard.get("blocked") else "未触发")}｜软否决：{escape(soft_text)}<br>
              权重结果：{escape(weight_vote_text)}｜直接决策权重：{escape(_fmt_weight(weight_summary["direct_weight"]))}｜配置总权重：{escape(_fmt_weight(weight_summary["total_config_weight"]))}
            </div>
            <div class="status-card" style="margin-top:8px;">支持：{escape("、".join(supporting) if supporting else "暂无明确支持委员")}<br>反对/观望：{escape("、".join(opposing) if opposing else "暂无明确反对委员")}<br>否决：{escape("、".join(veto_members) if veto_members else "无")}</div>
          </div>
        </div>
        """
    )

    render_committee_full_summary_expander(decision, "查看完整委员会总结")

    with st.expander("委员会权重", expanded=False):
        st.markdown(_safe_committee_text(weight_text, 500))

    with st.expander("按委员权重投票明细", expanded=True):
        for row in list(weight_summary.get("rows") or []):
            st.markdown(
                f"**{row.get('name', '委员')}**｜{row.get('vote', '观望')}｜"
                f"权重 {_fmt_weight(float(row.get('weight', 0) or 0))}｜计入 {row.get('bucket', '-')}｜"
                f"方向 {row.get('direction', '中性')}｜信心 {row.get('confidence', 0)}｜"
                f"weighted_score {row.get('weighted_score', 0)}"
            )
        if not list(weight_summary.get("rows") or []):
            st.markdown("暂无委员权重明细。")

    with st.expander("外部 AI 正式投票复核", expanded=True):
        st.markdown(f"**DeepSeek**：{deepseek.get('status', '等待')}｜{deepseek.get('vote', '观望')}｜方向 {deepseek.get('direction_text', '中性')}｜耗时 {deepseek.get('duration_ms', 0)} ms")
        st.markdown(f"**Gemini**：{gemini.get('status', '等待')}｜{gemini.get('vote', '观望')}｜方向 {gemini.get('direction_text', '中性')}｜耗时 {gemini.get('duration_ms', 0)} ms")
        st.markdown(f"**一致性**：{ai_consensus.get('agreement', '数据不足')}｜综合风险 {ai_consensus.get('combined_risk_level', '中')}｜建议 {ai_consensus.get('suggested_adjustment', '不调整')}")
        st.markdown(f"**DeepSeek摘要**：{_safe_committee_text(deepseek_summary, 260)}")
        st.markdown(f"**Gemini摘要**：{_safe_committee_text(gemini_summary, 260)}")
        st.markdown(f"**外部AI综合**：{_safe_committee_text(ai_consensus.get('summary', '外部AI参与正式投票，但不直接执行交易。'), 260)}")
        st.markdown("外部 AI 当前参与正式权重投票，但不能直接执行交易，不能绕过风险委员和实盘安全委员。")

    with st.expander("委员会解释", expanded=True):
        st.markdown(f"**为什么通过或不通过**：{_safe_committee_text(explanation.get('why_pass_or_not', ''), 260)}")
        st.markdown(f"**当前最大风险**：{_safe_committee_text(explanation.get('max_risk', ''), 260)}")
        st.markdown(f"**下一步观察条件**：{_safe_committee_text(explanation.get('next_condition', ''), 260)}")
        st.markdown(f"**信号失效条件**：{_safe_committee_text(explanation.get('invalid_condition', decision.get('invalid_condition', '')), 260)}")

    with st.expander("主要理由 / 主要风险 / 最终警告", expanded=False):
        st.markdown("**主要理由**")
        for item in list(decision.get("main_reasons") or []):
            st.markdown(f"- {_safe_committee_text(item, 220)}")
        st.markdown("**主要风险**")
        for item in list(decision.get("main_risks") or []):
            st.markdown(f"- {_safe_committee_text(item, 220)}")
        st.markdown("**最终警告**")
        for item in list(decision.get("final_warnings") or []):
            st.markdown(f"- {_safe_committee_text(item, 220)}")

    render_html('<div class="app-shell"><div class="module-card"><div class="module-title">委员投票明细</div><div class="module-desc">每个委员独立判断，风险委员拥有最高否决权。</div>')
    for member in list(decision.get("member_votes") or []):
        member_name = str(member.get("member_name", "委员"))
        vote = str(member.get("vote", "建议观望"))
        veto = "是" if member.get("veto") else "否"
        member_weight = _committee_member_weight(member_name, weights)
        member_bucket = _committee_vote_bucket(member)
        member_type = "影子委员" if member.get("shadow") else "正式委员"
        member_reasons = _render_numbered(list(member.get("reasons") or []))
        member_risks = _render_numbered(list(member.get("risks") or []))
        with st.expander(f"{member_name}｜{member_type}｜{vote}｜权重 {_fmt_weight(member_weight)}｜计入 {member_bucket}｜否决 {veto}", expanded=False):
            render_html(
                f"""
                <div class="status-card">
                  身份：{escape(member_type)}｜权重：{_fmt_weight(member_weight)}｜计入：{escape(member_bucket)}｜否决：{escape(veto)}<br>
                  方向：{escape(str(member.get("direction_text", "中性")))}<br>
                  vote_strength：{escape(str(member.get("vote_strength", 0)))}｜weighted_score：{escape(str(member.get("weighted_score", 0)))}｜软警告：{escape("是" if member.get("soft_warning") else "否")}<br>
                  风险：{escape(str(member.get("risk_level", "中")))}<br>
                  总结：{escape(_safe_committee_text(member.get("summary", "")))}<br>
                  <b>理由</b><ol style="padding-left:18px;margin:6px 0 0 0;">{member_reasons}</ol>
                  <b>风险</b><ol style="padding-left:18px;margin:6px 0 0 0;">{member_risks}</ol>
                </div>
                """
            )
    render_html("</div></div>")


def render_sim_signal_linkage(decision: dict[str, Any]) -> None:
    """信号页委员会结果与模拟交易执行器联动。"""
    if not decision:
        return
    symbol = str(decision.get("symbol") or st.session_state.get("current_symbol", "BTCUSDT"))
    ticker = market_cache.get_ticker(symbol)
    price = float((ticker or {}).get("last_price") or 0)
    signal = committee_decision_to_sim_signal(decision)
    summary = get_sim_account_summary()
    positions = [p for p in summary.get("positions", []) if p.get("status") in {"open", "partially_closed"} and p.get("symbol") == symbol]
    orders = [o for o in summary.get("orders", []) if o.get("status") == "pending" and o.get("symbol") == symbol]
    ok, reasons = validate_signal_for_simulation(signal, {symbol: price})
    status = "可进入模拟交易候选" if ok else "暂不可进入模拟交易"
    color = "green" if ok else "yellow"
    reason_text = "已满足委员会与模拟风控条件。" if ok else "；".join(reasons)
    st.markdown(
        f"""
        <div class="app-shell">
          <div class="module-card">
            <div class="module-title">模拟交易联动</div>
            <div class="committee-grid">
              <div class="summary-card"><div class="summary-label">模拟状态</div><div class="summary-value {color}">{escape(status)}</div></div>
              <div class="summary-card"><div class="summary-label">当前价格</div><div class="summary-value">{format_price(price)}</div></div>
              <div class="summary-card"><div class="summary-label">同币种持仓</div><div class="summary-value">{len(positions)}</div></div>
              <div class="summary-card"><div class="summary-label">待触发订单</div><div class="summary-value">{len(orders)}</div></div>
            </div>
            <div class="status-card" style="margin-top:8px;">{escape(reason_text)}</div>
          </div>
        </div>
        """,
        unsafe_allow_html=True,
    )
    if ok and st.button("加入模拟候选 / 创建本地模拟订单", key=f"sim_link_{symbol}", use_container_width=True):
        order = create_pending_sim_order(signal, price)
        if order:
            st.success("已创建本地模拟订单。")
        else:
            st.warning("未创建模拟订单，请查看模拟事件日志。")
        st.rerun()


def render_manual_position_override_panel(decision: dict[str, Any]) -> None:
    """人工仓位干预层：只能在风控允许范围内记录用户选择。"""
    if not decision:
        return
    symbol = str(decision.get("symbol") or st.session_state.get("current_symbol", "BTCUSDT"))
    ticker = market_cache.get_ticker(symbol) or {}
    price = ticker.get("last_price")
    base_eval = evaluate_manual_position_override(decision, 0)
    risk_max = float(base_eval.get("risk_max_position_pct", 0) or 0)
    system_pct = float(base_eval.get("system_position_pct", 0) or 0)
    allowed_hint = bool(decision.get("manual_override_allowed") and risk_max > 0)
    st.markdown(
        f"""
        <div class="app-shell">
          <div class="module-card">
            <div class="module-title">人工仓位干预层</div>
            <div class="module-desc">用户只能在风控允许范围内调整仓位；硬否决、数据质量差、安全锁和风控限制不能被绕过。</div>
            <div class="committee-grid">
              <div class="summary-card"><div class="summary-label">系统建议仓位</div><div class="summary-value yellow">{escape(str(decision.get("system_position_suggestion", decision.get("position_suggestion", "0%"))))}</div></div>
              <div class="summary-card"><div class="summary-label">系统建议中值</div><div class="summary-value">{system_pct:.2f}%</div></div>
              <div class="summary-card"><div class="summary-label">风控最大仓位</div><div class="summary-value {'green' if risk_max > 0 else 'red'}">{risk_max:.2f}%</div></div>
              <div class="summary-card"><div class="summary-label">人工调整</div><div class="summary-value {'green' if allowed_hint else 'red'}">{'允许' if allowed_hint else '不允许'}</div></div>
            </div>
          </div>
        </div>
        """,
        unsafe_allow_html=True,
    )
    user_pct = st.slider("用户选择仓位（%）", min_value=0.0, max_value=max(risk_max, 1.0), value=min(system_pct, risk_max) if risk_max else 0.0, step=0.25, key=f"manual_pos_{symbol}", disabled=not allowed_hint)
    preview = evaluate_manual_position_override(decision, user_pct, confirmed=False)
    if preview.get("requires_confirmation"):
        st.warning("你选择的仓位高于系统建议仓位，继续前必须二次确认，本次操作会被标记为人工仓位干预。")
        external_ai = decision.get("external_ai") or {}
        soft_members = [name for name, row in [("DeepSeek", external_ai.get("deepseek") or {}), ("Gemini", external_ai.get("gemini") or {})] if row.get("soft_veto")]
        if soft_members:
            st.warning(f"外部AI存在风险提醒：{'、'.join(soft_members)} 提出软否决或降仓建议。你仍选择提高仓位，本次操作将被记录为人工干预。")
    confirmed = st.checkbox("我确认理解风险，并愿意承担该仓位调整带来的后果", key=f"manual_confirm_{symbol}", disabled=not allowed_hint)
    confirm_text = st.text_input("实盘模式确认短句预留：我确认承担本次仓位调整风险", key=f"manual_confirm_text_{symbol}", disabled=not allowed_hint)
    final_eval = evaluate_manual_position_override(decision, user_pct, confirmed=confirmed, confirm_text=confirm_text)
    if final_eval.get("allowed"):
        st.success(final_eval.get("message"))
    else:
        st.warning(final_eval.get("message"))
    if st.button("记录人工仓位选择", key=f"save_manual_override_{symbol}", use_container_width=True, disabled=not allowed_hint):
        row = save_manual_position_override(decision, final_eval, mode="模拟", current_price=price, confirm_text=confirm_text)
        if final_eval.get("allowed"):
            st.success(f"人工仓位干预已记录：{row.get('user_selected_position')}%。")
        else:
            st.error(f"人工仓位干预被拒绝并已记录：{row.get('reason')}")


def render_committee_candidates() -> None:
    """渲染观察池进入委员会的候选对象。"""
    candidates = get_committee_candidates()[:10]
    st.markdown('<div class="app-shell"><div class="module-card"><div class="module-title">委员会候选榜</div><div class="module-desc">来源于观察池重点跟踪对象，点击后切换为当前交易对象。</div>', unsafe_allow_html=True)
    if not candidates:
        st.markdown('<div class="status-card">暂无符合条件的委员会候选对象。观察池进入重点跟踪后会自动出现在这里。</div>', unsafe_allow_html=True)
    for index, row in enumerate(candidates, start=1):
        symbol = str(row.get("symbol", "-"))
        cols = st.columns([1.1, .8, .8, .8])
        with cols[0]:
            st.markdown(f"**#{index} {symbol}**  \n{row.get('strategy_name', '-')}")
        with cols[1]:
            st.markdown(f"观察 {row.get('watch_score', 0)}  \n{row.get('status', '-')}")
        with cols[2]:
            st.markdown(f"置信 {row.get('confidence', 0)}  \n风险 {row.get('risk_score', 0)}")
        with cols[3]:
            if st.button("提交委员会", key=f"committee_candidate_{symbol}_{index}", use_container_width=True):
                set_current_symbol(symbol)
                st.query_params["page"] = st.session_state.active_page
                st.query_params["symbol"] = symbol
    st.markdown("</div></div>", unsafe_allow_html=True)


def _fmt_strategy_price(value: Any) -> str:
    """策略模块价格格式。"""
    try:
        number = float(value)
    except (TypeError, ValueError):
        return "待确认"
    if number <= 0:
        return "待确认"
    return format_price(number)


def render_signal_analysis(symbol: str, ticker: dict[str, Any] | None) -> None:
    """渲染市场结构、评分、方向建议与止盈止损建议。"""
    symbol = st.session_state.get("current_symbol", symbol)
    interval = market_cache.get_kline_interval()
    rows = market_cache.get_klines(symbol, interval)
    live_ticker = get_effective_ticker(symbol) or ticker
    current_price = live_ticker.get("last_price") if live_ticker else None
    orderbook = market_cache.get_orderbook(symbol)
    orderbook_analysis = analyze_orderbook(orderbook, current_price)
    analysis = build_signal_analysis(live_ticker, rows, orderbook_analysis)
    derivatives = market_cache.get_derivatives(symbol)
    capital = analyze_capital_structure(derivatives, live_ticker, analysis)
    liquidation = analyze_liquidation_risk(live_ticker, rows, derivatives, orderbook_analysis, analysis)
    whale = market_cache.get_whales(symbol)
    signal_missing = []
    if not live_ticker:
        signal_missing.append("实时价格等待刷新")
    if len(rows) < 80:
        signal_missing.append(f"K线数据不足：当前 {len(rows)} 根 / 需要 80 根")
    if not orderbook:
        signal_missing.append("盘口等待刷新")
    if not whale:
        signal_missing.append("暂无明显大单")
    if signal_missing:
        append_debug_log(SIGNAL_CHAIN_LOG, f"signal_chain_missing symbol={symbol} reasons={' | '.join(signal_missing)}")
    dealer = analyze_dealer_behavior(whale, derivatives, orderbook_analysis, analysis, liquidation)
    radar = analyze_market_risk_radar(live_ticker, rows, derivatives, capital, liquidation, whale, dealer, orderbook_analysis, analysis)
    strategy = build_local_strategy(
        symbol=symbol,
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
    market_cognition = build_market_cognition(
        symbol=symbol,
        ticker=live_ticker,
        rows=rows,
        derivatives=derivatives,
        orderbook_analysis=orderbook_analysis,
        whale=whale,
        signal_analysis=analysis,
        local_strategy=strategy,
        interval_base=interval,
    )
    if is_watched(symbol):
        try:
            update_watchlist_item(symbol, strategy, live_ticker)
        except Exception as exc:
            print(f"[观察池] 更新 {symbol} 失败 error={repr(exc)}")
    strategy_key = f"{strategy.get('symbol')}|{strategy.get('timestamp')[:16]}|{strategy.get('action')}|{strategy.get('confidence')}"
    if st.session_state.get("last_strategy_log_key") != strategy_key:
        append_strategy_log(strategy)
        st.session_state.last_strategy_log_key = strategy_key
    snapshot = market_cache.snapshot()

    rsi_text = f"{analysis['rsi']:.2f}" if analysis.get("rsi") is not None else "等待数据"
    ma20_text = format_price(analysis["ma20"]) if analysis.get("ma20") else "等待数据"
    ma60_text = format_price(analysis["ma60"]) if analysis.get("ma60") else "等待数据"
    support_text = format_price(analysis["support"]) if analysis.get("support") else "等待数据"
    resistance_text = format_price(analysis["resistance"]) if analysis.get("resistance") else "等待数据"
    volume_change = float(analysis.get("volume_change") or 0)
    structure = str(analysis["market_structure"])
    suggestion = str(analysis["suggestion"])
    trend_level = str(analysis["trend_level"])
    risk_level = str(analysis["risk_level"])
    oi = (derivatives or {}).get("oi") or {}
    funding = (derivatives or {}).get("funding") or {}
    long_short = (derivatives or {}).get("long_short") or {}
    oi_changes = oi.get("changes") or {}
    oi_text = format_compact(oi.get("current_oi")) if oi.get("current_oi") is not None else "待确认"
    funding_text = _format_funding(funding.get("rate"))
    account_ratio_text = _format_ratio(long_short.get("account_ratio"))
    position_ratio_text = _format_ratio(long_short.get("position_ratio"))
    derivatives_error = snapshot.get("derivatives_last_error") or ""
    derivatives_error_html = ""
    if derivatives_error:
        derivatives_error_html = f'<div class="status-card" style="margin-top:8px;color:#F0B90B;">{escape(str(derivatives_error))}</div>'
    capital_score_text = f'{capital["score"]} / 100 · {escape(str(capital["level"]))}'
    capital_state_text = escape(str(capital["state"]))
    capital_explanation_text = escape(str(capital["explanation"]))
    derivatives_update_time = escape(str(snapshot.get("derivatives_last_update_time", "初始化中")))

    render_metric_grid(
        [
            ("市场结构", structure, _signal_color(structure)),
            ("趋势评分", f"{analysis['trend_score']} / 100", _signal_color(trend_level)),
            ("趋势等级", trend_level, _signal_color(trend_level)),
            ("交易建议", suggestion, _signal_color(suggestion)),
            ("风险评分", f"{analysis['risk_score']} / 100", _signal_color(risk_level)),
            ("风险等级", risk_level, _signal_color(risk_level)),
            ("综合风险", f"{radar['overall_score']} / 100", _signal_color(str(radar["risk_level"]))),
            ("交易安全", str(radar["trade_safety"]), _signal_color(str(radar["trade_safety"]))),
            ("RSI", rsi_text, "blue"),
            ("MACD", str(analysis["macd_signal"]), _signal_color(str(analysis["macd_signal"]))),
            ("支撑位", support_text, "green"),
            ("压力位", resistance_text, "red"),
            ("资金结构评分", f"{capital['score']} / 100", _signal_color(str(capital["level"]))),
            ("衍生品状态", str(capital["market_state"]), _signal_color(str(capital["market_state"]))),
            ("爆仓风险评分", f"{liquidation['risk_score']} / 100", _signal_color(str(liquidation["risk_level"]))),
            ("挤仓风险", str(liquidation["squeeze_state"]), _signal_color(str(liquidation["squeeze_state"]))),
            ("大单强度", f"{(whale or {}).get('score', 0)} / 100", _signal_color(str((whale or {}).get("level", "大单中性")))),
            ("庄家行为", str(dealer["state"]), _signal_color(str(dealer["state"]))),
            ("本地策略", str(strategy["action"]), _signal_color(str(strategy["action"]))),
            ("策略置信度", f"{strategy['confidence']} / 100", _signal_color(str(strategy["action"]))),
            ("本地投票", f"{strategy.get('local_vote_score', 0)} / 100 · {strategy.get('local_vote_grade', 'D')}", _signal_color(str(strategy.get("local_vote_decision", "只观察")))),
            ("投票决议", str(strategy.get("local_vote_decision", "只观察")), _signal_color(str(strategy.get("local_vote_decision", "只观察")))),
        ]
    )

    data_status = ""
    if signal_missing:
        data_status = f'<div class="status-card" style="margin-bottom:8px;color:#F0B90B;">{"<br>".join(escape(str(item)) for item in signal_missing)}</div>'
    elif not analysis.get("ready"):
        data_status = f'<div class="status-card" style="margin-bottom:8px;color:#F0B90B;">{escape(str(analysis["message"]))}</div>'

    render_local_strategy_decision(strategy)
    render_market_cognition_panel(market_cognition)
    experience_version = get_selected_experience_library_version()
    experience_mode = get_selected_experience_mode()
    committee_decision = run_committee_meeting(
        symbol,
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
        market_cognition=market_cognition,
        experience_mode=experience_mode,
        experience_library_version=experience_version,
        experience_library_path=EXPERIENCE_LIBRARY_VERSIONS.get(experience_version, ""),
        experience_library_data_sources=(
            "current + funding_v1 + oi_longshort_recent30_v1"
            if experience_mode == "fused"
            else get_experience_library_data_sources(experience_version)
        ),
    )
    market_cognition_with_committee = dict(market_cognition)
    market_cognition_with_committee["committee_final_action"] = committee_decision.get("final_action")
    market_cognition_with_committee["committee_trade_value_score"] = (committee_decision.get("trading_committee_v91") or {}).get("trade_value_score")
    market_cognition_with_committee["risk_judge_verdict"] = ((committee_decision.get("trading_committee_v91") or {}).get("risk_judge") or {}).get("risk_verdict")
    market_cognition_with_committee["position_plan_summary"] = ((committee_decision.get("trading_committee_v91") or {}).get("position_plan") or {}).get("reason")
    market_cognition_with_committee["execution_plan_summary"] = ((committee_decision.get("trading_committee_v91") or {}).get("execution_plan") or {}).get("reason")
    save_market_cognition_snapshot(market_cognition_with_committee)
    render_ai_committee_decision(committee_decision)
    render_sim_signal_linkage(committee_decision)
    render_manual_position_override_panel(committee_decision)
    render_committee_candidates()

    st.markdown(
        f"""
        <div class="app-shell">
          <div class="module-card">
            <div class="module-title">衍生品市场结构</div>
            <div class="metric-value {_signal_color(str(capital["market_state"]))}">{escape(str(capital["market_state"]))}</div>
            <div class="module-desc">{escape(str(capital["market_explanation"]))}</div>
            <div class="status-card" style="margin-top:8px;">
              当前OI：{oi_text}<br>
              OI 5分钟变化：{_format_signed_percent(oi_changes.get("5m"))}<br>
              OI 15分钟变化：{_format_signed_percent(oi_changes.get("15m"))}<br>
              OI 1小时变化：{_format_signed_percent(oi_changes.get("1h"))}<br>
              OI 4小时变化：{_format_signed_percent(oi_changes.get("4h"))}<br>
              OI 24小时变化：{_format_signed_percent(oi_changes.get("24h"))}<br>
              OI状态：{escape(str(oi.get("status", "等待数据")))}
            </div>
            <div class="status-card" style="margin-top:8px;">
              Funding：{funding_text}<br>
              Funding状态：{escape(str(funding.get("status", "等待数据")))}｜趋势：{escape(str(funding.get("trend", "等待数据")))}<br>
              账户多空比：{account_ratio_text}<br>
              持仓多空比：{position_ratio_text}<br>
              多空状态：{escape(str(long_short.get("status", "等待数据")))}
            </div>
            <div class="status-card" style="margin-top:8px;">
              资金结构评分：{capital_score_text}<br>
              资金结构状态：{capital_state_text}<br>
              中文解释：{capital_explanation_text}<br>
              更新时间：{derivatives_update_time}
            </div>
            {derivatives_error_html}
          </div>
        </div>
        """,
        unsafe_allow_html=True,
    )

    st.markdown(
        f"""
        <div class="app-shell">
          <div class="module-card">
            <div class="module-title">清算热力区 / 爆仓风险分析</div>
            <div class="metric-value {_signal_color(str(liquidation["risk_level"]))}">{liquidation["risk_score"]} / 100 · {escape(str(liquidation["risk_level"]))}</div>
            <div class="module-desc">{escape(str(liquidation["explanation"]))}</div>
            <div class="status-card" style="margin-top:8px;">
              当前价格：{format_price(liquidation["current_price"]) if liquidation.get("current_price") else "等待数据"}<br>
              最近清算区：{escape(str(liquidation["nearest_zone"]))}<br>
              上方清算区：{escape(str(liquidation["upper_zone"]))}<br>
              下方清算区：{escape(str(liquidation["lower_zone"]))}
            </div>
            <div class="status-card" style="margin-top:8px;">
              上方距离：{escape(str(liquidation["upper_distance"]))}｜预计触发强度：{escape(str(liquidation["upper_strength"]))}<br>
              下方距离：{escape(str(liquidation["lower_distance"]))}｜预计触发强度：{escape(str(liquidation["lower_strength"]))}<br>
              当前挤仓风险：{escape(str(liquidation["squeeze_state"]))}
            </div>
            <div class="status-card" style="margin-top:8px;">
              猎杀止损概率：{escape(str(liquidation["hunt_probability"]))}<br>
              中文解释：{escape(str(liquidation["hunt_explanation"]))}
            </div>
          </div>
        </div>
        """,
        unsafe_allow_html=True,
    )

    whale = whale or {}

    st.markdown(
        f"""
        <div class="app-shell">
          <div class="module-card">
            <div class="module-title">庄家行为初判</div>
            <div class="metric-value {_signal_color(str(dealer["state"]))}">{escape(str(dealer["state"]))}</div>
            <div class="module-desc">{escape(str(dealer["explanation"]))}</div>
            <div class="status-card" style="margin-top:8px;">
              吸筹概率：{dealer["accumulation_probability"]}%<br>
              洗盘概率：{dealer["wash_probability"]}%<br>
              拉升概率：{dealer["markup_probability"]}%<br>
              派发概率：{dealer["distribution_probability"]}%
            </div>
          </div>
        </div>
        """,
        unsafe_allow_html=True,
    )

    risk_components_html = []
    for component in radar.get("components", []):
        risk_components_html.append(
            f"""<div class="status-card" style="margin-top:8px;">
              <b>{escape(str(component.get("name", "风险分项")))}</b>：{component.get("score", 0)} / 100 · {escape(str(component.get("level", "中")))}<br>
              原因：{escape(str(component.get("reason", "等待数据")))}
            </div>"""
        )
    alerts_html = "".join(
        f'<div class="status-card" style="margin-top:8px;color:#F0B90B;">{escape(str(alert))}</div>'
        for alert in radar.get("alerts", [])
    )

    st.markdown(
        f"""
        <div class="app-shell">
          <div class="module-card">
            <div class="module-title">市场风险雷达</div>
            <div class="metric-value {_signal_color(str(radar["risk_level"]))}">{radar["overall_score"]} / 100 · {escape(str(radar["risk_level"]))}</div>
            <div class="module-desc">{escape(str(radar["market_explanation"]))}</div>
            <div class="status-card" style="margin-top:8px;">
              市场状态：{escape(str(radar["market_state"]))}<br>
              交易安全等级：{escape(str(radar["trade_safety"]))}<br>
              建议仓位：{escape(str(radar["position_size"]))}<br>
              仓位解释：{escape(str(radar["position_explanation"]))}
            </div>
            <div class="module-title" style="margin-top:10px;">风险来源拆解</div>
            {''.join(risk_components_html)}
            <div class="module-title" style="margin-top:10px;">风险警报</div>
            {alerts_html}
          </div>
        </div>
        """,
        unsafe_allow_html=True,
    )

    st.markdown(
        f"""
        <div class="app-shell">
          {data_status}
          <div class="module-grid">
            <div class="module-card">
              <div class="module-title">市场结构分析</div>
              <div class="metric-value {_signal_color(structure)}">{escape(structure)}</div>
              <div class="module-desc">{escape(str(analysis["structure_explanation"]))}</div>
              <div class="status-card" style="margin-top:8px;">
                当前交易对象：{escape(symbol)}<br>
                当前周期：{escape(interval)}<br>
                当前价格：{format_price(current_price) if current_price else "等待数据"}
              </div>
            </div>
            <div class="module-card">
              <div class="module-title">趋势评分</div>
              <div class="metric-value {_signal_color(trend_level)}">{analysis["trend_score"]} / 100 · {escape(trend_level)}</div>
              <div class="module-desc">{escape(str(analysis["trend_explanation"]))}</div>
              <div class="status-card" style="margin-top:8px;">
                MA20：{ma20_text}<br>
                MA60：{ma60_text}<br>
                成交量变化：{volume_change:.1f}%
              </div>
            </div>
            <div class="module-card">
              <div class="module-title">风险评分</div>
              <div class="metric-value {_signal_color(risk_level)}">{analysis["risk_score"]} / 100 · {escape(risk_level)}</div>
              <div class="module-desc">{escape(str(analysis["risk_explanation"]))}</div>
              <div class="status-card" style="margin-top:8px;">
                支撑位：{support_text}<br>
                压力位：{resistance_text}<br>
                盘口倾向：{escape(str(orderbook_analysis.get("bias", "等待数据")))}
              </div>
            </div>
            <div class="module-card">
              <div class="module-title">交易方向与止盈止损</div>
              <div class="metric-value {_signal_color(suggestion)}">{escape(suggestion)}</div>
              <div class="module-desc">该建议只用于行情分析与学习，不代表真实下单指令。</div>
              <div class="status-card" style="margin-top:8px;">
                入场参考：{escape(str(analysis["entry_zone"]))}<br>
                止损位：{escape(str(analysis["stop_loss"]))}<br>
                止盈1：{escape(str(analysis["take_profit_1"]))}<br>
                止盈2：{escape(str(analysis["take_profit_2"]))}<br>
                风险收益比：{escape(str(analysis["risk_reward"]))}
              </div>
            </div>
            <div class="module-card">
              <div class="module-title">当前信号来源</div>
              <ol class="module-desc" style="padding-left:18px;margin:8px 0 0 0;">{_render_numbered(analysis["reasons"])}</ol>
            </div>
            <div class="module-card">
              <div class="module-title">当前风险提示</div>
              <ol class="module-desc" style="padding-left:18px;margin:8px 0 0 0;">{_render_numbered(analysis["risks"])}</ol>
            </div>
          </div>
        </div>
        """,
        unsafe_allow_html=True,
    )


def render_signals(symbol: str, ticker: dict[str, Any] | None, scores: dict[str, Any]) -> None:
    """信号页。"""
    render_page_head("signals")
    render_watchlist_quick_controls(st.session_state.get("current_symbol", symbol), "signals", source="manual")
    fragment = getattr(st, "fragment", None) or getattr(st, "experimental_fragment", None)
    live_symbol = st.session_state.get("current_symbol", symbol)
    live_ticker = market_cache.get_ticker(live_symbol) or ticker
    if fragment:
        if st.session_state.get("chart_interactive"):
            render_kline_system(live_symbol)
        else:
            @fragment(run_every="8s")
            def _live_kline_module() -> None:
                live_symbol = st.session_state.get("current_symbol", symbol)
                render_kline_system(live_symbol)

            _live_kline_module()

        @fragment(run_every="3s")
        def _live_orderbook_module() -> None:
            live_symbol = st.session_state.get("current_symbol", symbol)
            live_ticker = market_cache.get_ticker(live_symbol) or ticker
            render_orderbook_system(live_symbol, live_ticker)

        _live_orderbook_module()

        @fragment(run_every="5s")
        def _live_signal_analysis() -> None:
            live_symbol = st.session_state.get("current_symbol", symbol)
            live_ticker = market_cache.get_ticker(live_symbol) or ticker
            render_signal_analysis(live_symbol, live_ticker)

        _live_signal_analysis()
    else:
        render_kline_system(live_symbol)
        render_orderbook_system(live_symbol, live_ticker)
        render_signal_analysis(live_symbol, live_ticker)


def render_trading() -> None:
    """交易页。"""
    render_page_head("trading")
    symbol = st.session_state.get("current_symbol", "BTCUSDT")
    ticker = market_cache.get_ticker(symbol)
    price = valid_price((ticker or {}).get("last_price"))
    decision = build_current_committee_decision(symbol, ticker)
    signal = committee_decision_to_sim_signal(decision)
    price_map = build_sim_price_map(symbol)
    if price is not None:
        price_map[symbol] = price
    settings = load_settings()
    if settings.get("mode") == "auto":
        summary = update_simulation(price_map, [signal])
    else:
        summary = update_simulation(price_map, [])
    account = summary.get("account", {})
    positions = [p for p in summary.get("positions", []) if p.get("status") in {"open", "partially_closed"}]
    orders = [o for o in summary.get("orders", []) if o.get("status") == "pending"]
    history = summary.get("history", [])
    events = summary.get("events", [])
    stats = summary.get("stats", {})
    ok, reject_reasons = validate_signal_for_simulation(signal, {symbol: price or 0})

    def money(value: Any) -> str:
        return f"{float(value or 0):,.2f} USDT"

    def pct(value: Any) -> str:
        return f"{float(value or 0):+.2f}%"

    def direction_text(value: Any) -> str:
        return "空单" if str(value) == "short" else "多单"

    def seconds_text(value: Any) -> str:
        seconds = max(0, int(float(value or 0)))
        minutes = seconds // 60
        if minutes < 60:
            return f"{minutes}分钟"
        return f"{minutes // 60}小时{minutes % 60}分钟"

    def remaining_text(order: dict[str, Any]) -> str:
        remaining = int(order.get("expired_ts", 0) or 0) - int(__import__("time").time())
        if remaining <= 0:
            return "已到期"
        return seconds_text(remaining)

    def distance_to_entry(order: dict[str, Any], current_price: Any) -> str:
        price_value = valid_price(current_price)
        low = valid_price(order.get("entry_zone_low"))
        high = valid_price(order.get("entry_zone_high"))
        if price_value is None or low is None or high is None:
            return "等待价格刷新"
        if low <= price_value <= high:
            return "已进入入场区"
        target = low if price_value < low else high
        return f"{abs(price_value - target) / price_value * 100:.2f}%"

    def render_sim_trade_review_panel() -> None:
        reviews = load_sim_trade_reviews(100)
        review_summary = load_feedback_summary() or build_feedback_summary(reviews)
        version_stats = review_summary.get("by_experience_library_version") or {}
        render_metric_grid(
            [
                ("总交易数", str(review_summary.get("total_trades", 0)), "blue"),
                ("盈利交易数", str(review_summary.get("winning_trades", 0)), "green"),
                ("亏损交易数", str(review_summary.get("losing_trades", 0)), "red"),
                ("胜率", f"{float(review_summary.get('win_rate', 0) or 0):.2f}%", "green" if float(review_summary.get("win_rate", 0) or 0) >= 50 else "yellow"),
                ("平均收益", pct(review_summary.get("avg_return_pct")), "green" if float(review_summary.get("avg_return_pct", 0) or 0) >= 0 else "red"),
                ("平均最大浮盈", f"{float(review_summary.get('avg_max_favorable_excursion', 0) or 0):+.2f}%", "green"),
                ("平均最大浮亏", f"{float(review_summary.get('avg_max_adverse_excursion', 0) or 0):+.2f}%", "red"),
                ("止盈次数", str(review_summary.get("take_profit_count", 0)), "green"),
                ("止损次数", str(review_summary.get("stop_loss_count", 0)), "red"),
                ("验证经验库", str(review_summary.get("experience_validated_count", 0)), "green"),
                ("推翻经验库", str(review_summary.get("experience_invalidated_count", 0)), "red"),
                ("样本不足但盈利", str(review_summary.get("unknown_but_profit_count", 0)), "yellow"),
                ("止损过紧", str(review_summary.get("tight_stop_loss_count", 0)), "red"),
                ("止盈保守", str(review_summary.get("conservative_take_profit_count", 0)), "yellow"),
            ]
        )
        version_cards = "".join(
            f"""<div class="summary-card">
              <div class="summary-label">{escape(version)} 经验库表现</div>
              <div class="summary-value {'green' if float(item.get('total_pnl_usdt', 0) or 0) >= 0 else 'red'}">{float(item.get('total_pnl_usdt', 0) or 0):+.2f} USDT</div>
              <div class="module-desc">交易 {int(item.get('trades', 0) or 0)}｜胜率 {float(item.get('win_rate', 0) or 0):.2f}%｜平均收益 {float(item.get('avg_pnl_pct', 0) or 0):+.2f}%</div>
            </div>"""
            for version, item in version_stats.items()
        )
        st.markdown(
            f"""
            <div class="app-shell"><div class="module-card">
              <div class="module-title">经验库版本交易表现</div>
              <div class="committee-grid">{version_cards or '<div class="status-card">暂无按经验库版本统计的平仓记录。</div>'}</div>
            </div></div>
            """,
            unsafe_allow_html=True,
        )
        if not reviews:
            st.info("暂无模拟交易复盘记录。新开仓会记录开仓快照，最终平仓后会写入完整复盘。")
            return
        st.markdown('<div class="app-shell"><div class="module-card"><div class="module-title">最近模拟交易复盘</div>', unsafe_allow_html=True)
        for review in reviews[:50]:
            open_snapshot = review.get("open_snapshot") or {}
            progress = review.get("position_progress") or {}
            close_result = review.get("close_result") or {}
            feedback = review.get("experience_feedback") or {}
            pnl_pct = float(close_result.get("final_pnl_pct", 0) or 0)
            pnl_usdt = float(close_result.get("final_pnl_usdt", 0) or 0)
            state_code = str(open_snapshot.get("state_code") or "-")
            feedback_label = str(feedback.get("experience_feedback_label") or "持仓中")
            color = "green" if pnl_usdt > 0 else "red" if pnl_usdt < 0 else "yellow"
            st.markdown(
                f"""
                <div class="status-card">
                  <b>{kline_symbol_link(open_snapshot.get("symbol"), str(open_snapshot.get("symbol") or "-"))}</b>｜{direction_text(open_snapshot.get("side"))}｜{escape(str(close_result.get("close_reason") or review.get("status", "open")))}｜
                  <span class="{color}">{pnl_usdt:+.2f} USDT / {pnl_pct:+.2f}%</span><br>
                  入场：{format_waiting_price(open_snapshot.get("entry_price"))}　出场：{format_waiting_price(close_result.get("close_price"))}　持仓：{float(close_result.get("holding_minutes", progress.get("holding_minutes", 0)) or 0):.1f}分钟<br>
                  最大浮盈：{float(progress.get("max_favorable_excursion", 0) or 0):+.2f}%　最大浮亏：{float(progress.get("max_adverse_excursion", 0) or 0):+.2f}%　部分止盈：{int(progress.get("partial_close_count", 0) or 0)}次<br>
                  经验库：{escape(str(open_snapshot.get("experience_library_version") or "-"))}　state_code：{escape(state_code)}　经验委员：{escape(str(open_snapshot.get("experience_vote") or "-"))} / Confidence {float(open_snapshot.get("experience_confidence", 0) or 0):.1f}<br>
                  经验反馈：<span class="{_signal_color(feedback_label)}">{escape(feedback_label)}</span>｜{escape(str(feedback.get("feedback_reason") or "持仓中，等待最终平仓后判断。"))}
                </div>
                """,
                unsafe_allow_html=True,
            )
        st.markdown("</div></div>", unsafe_allow_html=True)

    st.markdown(
        '<div class="app-shell"><div class="module-card warning-box"><b>模拟交易安全提示</b><br>当前为模拟交易，不会使用真实资金，不会执行真实订单。所有订单、持仓和盈亏均为本地模拟数据。</div></div>',
        unsafe_allow_html=True,
    )

    c1, c2, c3, c4 = st.columns(4)
    if c1.button("开启模拟交易", use_container_width=True):
        settings["mode"] = "auto"
        save_settings(settings)
        set_sim_status("running")
        st.rerun()
    if c2.button("暂停模拟交易", use_container_width=True):
        set_sim_status("paused")
        st.rerun()
    if c3.button("停止模拟交易", use_container_width=True):
        set_sim_status("stopped")
        st.rerun()
    if c4.button("重置模拟账户", use_container_width=True):
        reset_sim_account(float(settings.get("initial_balance", 1000)))
        st.rerun()

    status_color = "green" if account.get("status") == "running" else "yellow" if account.get("status") == "paused" else "red"
    render_metric_grid(
        [
            ("模拟交易状态", str(account.get("status", "stopped")), status_color),
            ("当前模式", "自动模拟" if settings.get("mode") == "auto" else "仅观察" if settings.get("mode") == "observe" else "手动确认", "blue"),
            ("当前权益", money(account.get("equity")), "green" if float(account.get("equity", 0) or 0) >= float(account.get("initial_balance", 0) or 0) else "red"),
            ("可用余额", money(account.get("available_balance")), ""),
            ("占用保证金", money(account.get("used_margin")), "yellow"),
            ("总名义仓位", money(account.get("total_exposure")), "blue"),
            ("浮动盈亏", money(account.get("unrealized_pnl")), "green" if float(account.get("unrealized_pnl", 0) or 0) >= 0 else "red"),
            ("累计收益率", pct(account.get("return_pct")), "green" if float(account.get("return_pct", 0) or 0) >= 0 else "red"),
            ("当前回撤", f"{float(account.get('current_drawdown', 0) or 0):.2f}%", "yellow"),
            ("最大回撤", f"{float(account.get('max_drawdown', 0) or 0):.2f}%", "yellow"),
            ("当前持仓", str(account.get("open_position_count", len(positions))), ""),
            ("待触发订单", str(account.get("pending_order_count", len(orders))), ""),
        ]
    )

    tabs = st.tabs(["账户总览", "当前持仓", "待触发订单", "交易历史", "统计分析", "模拟复盘", "参数设置", "事件日志"])

    with tabs[0]:
        risk = summary.get("risk_summary") or {}
        st.markdown(
            f"""
            <div class="app-shell"><div class="module-card">
              <div class="module-title">模拟账户中心</div>
              <div class="module-desc">当前为模拟交易，不会使用真实资金，不会执行真实订单。</div>
              <div class="committee-grid">
                <div class="summary-card"><div class="summary-label">初始资金</div><div class="summary-value">{money(account.get("initial_balance"))}</div></div>
                <div class="summary-card"><div class="summary-label">当前权益</div><div class="summary-value {_signal_color("支持交易" if float(account.get("equity", 0) or 0) >= float(account.get("initial_balance", 0) or 0) else "反对交易")}">{money(account.get("equity"))}</div></div>
                <div class="summary-card"><div class="summary-label">可用余额</div><div class="summary-value">{money(account.get("available_balance"))}</div></div>
                <div class="summary-card"><div class="summary-label">占用保证金</div><div class="summary-value yellow">{money(account.get("used_margin"))}</div></div>
                <div class="summary-card"><div class="summary-label">多头暴露</div><div class="summary-value green">{money(account.get("long_exposure"))}</div></div>
                <div class="summary-card"><div class="summary-label">空头暴露</div><div class="summary-value red">{money(account.get("short_exposure"))}</div></div>
                <div class="summary-card"><div class="summary-label">已实现盈亏</div><div class="summary-value {_signal_color("支持交易" if float(account.get("realized_pnl", 0) or 0) >= 0 else "反对交易")}">{money(account.get("realized_pnl"))}</div></div>
                <div class="summary-card"><div class="summary-label">今日盈亏</div><div class="summary-value {_signal_color("支持交易" if float(account.get("daily_pnl", 0) or 0) >= 0 else "反对交易")}">{money(account.get("daily_pnl"))}</div></div>
              </div>
              <div class="status-card" style="margin-top:8px;">
                账户风险状态：<b>{escape(str(risk.get("status", "正常")))}</b><br>
                当前持仓风险：{float(risk.get("total_risk_pct", 0) or 0):.2f}%｜预计最大止损亏损：{money(risk.get("total_risk_usdt"))}｜仍允许新开仓：{"是" if risk.get("allow_new_position") else "否"}<br>
                {escape(str(risk.get("explanation", "当前模拟账户风险等待计算。")))}
              </div>
            </div></div>
            """,
            unsafe_allow_html=True,
        )
        curve_rows = summary.get("equity_curve") or get_sim_equity_curve(300)
        if curve_rows:
            chart_values = {
                "权益": [float(row.get("equity", 0) or 0) for row in curve_rows],
                "累计盈亏": [float(row.get("total_pnl", 0) or 0) for row in curve_rows],
                "回撤": [float(row.get("current_drawdown", 0) or 0) for row in curve_rows],
            }
            st.line_chart(chart_values)
        else:
            st.info("当前暂无权益曲线，模拟账户变化后会自动生成。")

        st.markdown('<div class="app-shell"><div class="module-card"><div class="module-title">委员会通过信号</div>', unsafe_allow_html=True)
        st.markdown(
            f"""
            <div class="committee-grid">
              <div class="summary-card"><div class="summary-label">当前交易对象</div><div class="summary-value yellow">{escape(symbol)}</div></div>
              <div class="summary-card"><div class="summary-label">委员会动作</div><div class="summary-value {_signal_color(str(signal.get("action", "")))}">{escape(str(signal.get("action", "继续观察")))}</div></div>
              <div class="summary-card"><div class="summary-label">方向</div><div class="summary-value">{escape(str(decision.get("final_direction_text", "中性")))}</div></div>
              <div class="summary-card"><div class="summary-label">置信度</div><div class="summary-value blue">{signal.get("committee_confidence", 0)} / 100</div></div>
              <div class="summary-card"><div class="summary-label">风险评分</div><div class="summary-value yellow">{signal.get("risk_score", 0)} / 100</div></div>
              <div class="summary-card"><div class="summary-label">风险收益比</div><div class="summary-value">{signal.get("risk_reward_ratio") or "待确认"}</div></div>
              <div class="summary-card"><div class="summary-label">模拟订单状态</div><div class="summary-value">{escape("已有待触发订单" if any(o.get("symbol") == symbol for o in orders) else "未创建订单")}</div></div>
              <div class="summary-card"><div class="summary-label">模拟持仓状态</div><div class="summary-value">{escape("已有同币种持仓" if any(p.get("symbol") == symbol for p in positions) else "无同币种持仓")}</div></div>
            </div>
            <div class="status-card" style="margin-top:8px;">AI主席总结：{escape(_safe_committee_text(signal.get("chairman_summary", "等待委员会总结。"), 420))}</div>
            """,
            unsafe_allow_html=True,
        )
        if ok:
            st.success("该委员会信号当前满足模拟交易风控条件。")
            if price is None:
                st.warning("等待价格刷新，暂不创建模拟订单。")
            elif st.button("加入模拟候选 / 创建模拟订单", use_container_width=True):
                order = create_pending_sim_order(signal, price)
                if order:
                    st.success("已创建本地模拟订单。")
                else:
                    st.warning("未创建模拟订单，请查看模拟事件日志。")
                st.rerun()
        else:
            st.warning("当前未创建模拟订单：" + "；".join(reject_reasons))
        st.markdown("</div></div>", unsafe_allow_html=True)

    with tabs[1]:
        st.markdown('<div class="app-shell"><div class="module-card"><div class="module-title">当前模拟持仓中心</div>', unsafe_allow_html=True)
        if not positions:
            st.markdown('<div class="status-card">当前暂无模拟持仓。</div>', unsafe_allow_html=True)
        for pos in positions:
            pos_price = valid_price(price_map.get(str(pos.get("symbol", "")))) or valid_price(pos.get("current_price"))
            pnl = float(pos.get("unrealized_pnl", 0) or 0)
            pnl_class = "green" if pnl >= 0 else "red"
            r_value = calculate_position_r_multiple(pos, pos_price)
            holding = calculate_position_holding_time(pos)
            st.markdown(
                f"""
                <div class="status-card">
                  <b>{kline_symbol_link(pos.get("symbol"), str(pos.get("symbol")))}</b>｜{direction_text(pos.get("direction"))}｜模拟持仓｜{pos.get("status")}<br>
                  开仓：{format_waiting_price(pos.get("entry_price"))}　当前：{format_waiting_price(pos_price)}　数量：{float(pos.get("quantity", 0) or 0):.6f}<br>
                  模拟保证金：{money(pos.get("margin_usdt"))}　名义仓位：{money(pos.get("notional_usdt"))}　模拟杠杆：{pos.get("leverage", 1)}x<br>
                  浮动盈亏：<span class="{pnl_class}">{pnl:+.2f} USDT / {float(pos.get("unrealized_pnl_pct", 0) or 0):+.2f}%</span>　已实现：{money(pos.get("realized_pnl"))}<br>
                  止损：{format_waiting_price(pos.get("stop_loss"))}　止盈1：{format_waiting_price(pos.get("take_profit_1"))}　止盈2：{format_waiting_price(pos.get("take_profit_2"))}<br>
                  R倍数：{f"{r_value:+.2f}R" if r_value is not None else "暂无"}　持仓：{escape(str(holding.get("text", "0分钟")))}　止盈1：{"已触发" if pos.get("tp1_hit") else "未触发"}　保本止损：{"已移动" if pos.get("moved_stop_to_breakeven") else "未移动"}<br>
                  委员会：{escape(str(pos.get("committee_action", "等待")))} / 置信度{pos.get("committee_confidence", 0)} / 风险{pos.get("committee_risk_score", 0)}<br>
                  AI主席摘要：{escape(_safe_committee_text((pos.get("committee_snapshot") or {}).get("chairman_summary", pos.get("open_reason", "暂无摘要")), 420))}
                </div>
                """,
                unsafe_allow_html=True,
            )
            pc1, pc2, pc3, pc4 = st.columns(4)
            if pc1.button("全部平仓", key=f"close_all_{pos.get('position_id')}", use_container_width=True, disabled=pos_price is None):
                close_sim_position(str(pos.get("position_id")), "用户手动平仓", pos_price)
                st.rerun()
            if pc2.button("平仓50%", key=f"close_half_{pos.get('position_id')}", use_container_width=True, disabled=pos_price is None):
                close_sim_position(str(pos.get("position_id")), "用户手动平仓50%", pos_price, ratio=0.5)
                st.rerun()
            if pc3.button("止损到保本", key=f"breakeven_{pos.get('position_id')}", use_container_width=True):
                move_stop_to_breakeven(str(pos.get("position_id")))
                st.rerun()
            with pc4:
                st.write("详情")
            with st.expander(f"{pos.get('symbol')} 持仓详情与事件时间线", expanded=False):
                st.markdown(f"开仓原因：{pos.get('open_reason') or '暂无'}")
                st.markdown(f"信号失效条件：{pos.get('invalid_condition') or '暂无'}")
                st.markdown(f"本地策略动作：{pos.get('local_strategy_action') or '暂无'}")
                related_events = [e for e in events if e.get("symbol") == pos.get("symbol")]
                if not related_events:
                    st.info("当前暂无该持仓相关事件。")
                for event in related_events[:10]:
                    st.caption(f"{event.get('time')}｜{event.get('event_type')}｜{event.get('content') or event.get('reason')}")
        st.markdown("</div></div>", unsafe_allow_html=True)

    with tabs[2]:
        st.markdown('<div class="app-shell"><div class="module-card"><div class="module-title">待触发模拟订单中心</div>', unsafe_allow_html=True)
        if not orders:
            st.markdown('<div class="status-card">当前暂无待触发模拟订单。</div>', unsafe_allow_html=True)
        for order in orders[:50]:
            order_price = valid_price(price_map.get(str(order.get("symbol", ""))))
            st.markdown(
                f"""
                <div class="status-card">
                  <b>{kline_symbol_link(order.get("symbol"), str(order.get("symbol")))}</b>｜{direction_text(order.get("direction"))}｜{escape(str(order.get("action", "")))}｜待触发模拟订单<br>
                  当前价格：{format_waiting_price(order_price)}　入场区间：{format_waiting_price(order.get("entry_zone_low"))} - {format_waiting_price(order.get("entry_zone_high"))}　距离入场区：{escape(distance_to_entry(order, order_price))}<br>
                  止损：{format_waiting_price(order.get("stop_loss"))}　止盈1：{format_waiting_price(order.get("take_profit_1"))}　止盈2：{format_waiting_price(order.get("take_profit_2"))}<br>
                  建议仓位：{escape(str(order.get("position_pct", "0%")))}　预计保证金：{money(order.get("margin_usdt"))}　名义仓位：{money(order.get("notional_usdt"))}<br>
                  创建：{escape(str(order.get("created_time", "")))}　剩余有效期：{escape(remaining_text(order))}　来源：{escape(str(order.get("source", "AI交易委员会")))}
                </div>
                """,
                unsafe_allow_html=True,
            )
            oc1, oc2 = st.columns([1, 3])
            if oc1.button("取消订单", key=f"cancel_order_{order.get('order_id')}", use_container_width=True):
                cancel_order(str(order.get("order_id")))
                st.rerun()
            with oc2.expander("查看委员会信号摘要", expanded=False):
                snapshot = order.get("committee_snapshot") or {}
                st.markdown(f"主席总结：{escape(_safe_committee_text(snapshot.get('chairman_summary') or order.get('reason') or '暂无', 420))}", unsafe_allow_html=True)
                st.markdown(f"置信度：{snapshot.get('committee_confidence', 0)}｜风险：{snapshot.get('risk_score', 0)}｜风险收益比：{snapshot.get('risk_reward_ratio')}")
        st.markdown("</div></div>", unsafe_allow_html=True)

    with tabs[3]:
        if not history:
            st.info("当前暂无模拟交易历史。")
        else:
            filter_col1, filter_col2, filter_col3 = st.columns(3)
            result_filter = filter_col1.selectbox("结果筛选", ["全部", "盈利", "亏损"], key="sim_history_result_filter")
            dir_filter = filter_col2.selectbox("方向筛选", ["全部", "多单", "空单"], key="sim_history_dir_filter")
            reason_options = ["全部"] + sorted({str(row.get("close_reason") or "未知") for row in history})
            reason_filter = filter_col3.selectbox("原因筛选", reason_options, key="sim_history_reason_filter")
            rows = history
            if result_filter == "盈利":
                rows = [row for row in rows if row.get("is_win")]
            elif result_filter == "亏损":
                rows = [row for row in rows if not row.get("is_win")]
            if dir_filter == "多单":
                rows = [row for row in rows if row.get("direction") == "long"]
            elif dir_filter == "空单":
                rows = [row for row in rows if row.get("direction") == "short"]
            if reason_filter != "全部":
                rows = [row for row in rows if str(row.get("close_reason") or "未知") == reason_filter]
            for row in rows[:50]:
                pnl = float(row.get("pnl", 0) or 0)
                klass = "green" if pnl >= 0 else "red"
                st.markdown(
                    f"""
                    <div class="status-card">
                      <b>{kline_symbol_link(row.get("symbol"), str(row.get("symbol")))}</b>｜{direction_text(row.get("direction"))}｜{escape(str(row.get("close_reason", "")))}｜<span class="{klass}">{pnl:+.2f} USDT / {float(row.get("pnl_pct", 0) or 0):+.2f}%</span><br>
                      开仓：{format_price(row.get("entry_price"))}　平仓：{format_price(row.get("exit_price"))}　数量：{float(row.get("quantity", 0) or 0):.6f}　仓位：{money(row.get("notional_usdt"))}<br>
                      R倍数：{row.get("r_multiple") if row.get("r_multiple") not in {"", None} else "暂无"}　持仓：{seconds_text(row.get("holding_seconds"))}　委员会：{escape(str(row.get("committee_action", "")))} / 置信度{row.get("committee_confidence", 0)} / 风险{row.get("committee_risk_score", 0)}<br>
                      开仓时间：{escape(str(row.get("open_time", "")))}　平仓时间：{escape(str(row.get("close_time", "")))}
                    </div>
                    """,
                    unsafe_allow_html=True,
                )
        if st.button("清空模拟历史", use_container_width=True):
            clear_sim_history()
            st.rerun()

    with tabs[4]:
        render_metric_grid(
            [
                ("总交易次数", str(stats.get("total_trades", 0)), ""),
                ("当前持仓", str(len(positions)), ""),
                ("待触发订单", str(len(orders)), ""),
                ("盈利 / 亏损", f"{stats.get('wins', 0)} / {stats.get('losses', 0)}", ""),
                ("胜率", f"{float(stats.get('win_rate', 0)):.2f}%", "green" if float(stats.get("win_rate", 0)) >= 50 else "yellow"),
                ("累计盈亏", money(stats.get("total_pnl")), "green" if float(stats.get("total_pnl", 0) or 0) >= 0 else "red"),
                ("总收益率", pct(stats.get("return_pct", account.get("return_pct", 0))), "green" if float(stats.get("return_pct", account.get("return_pct", 0)) or 0) >= 0 else "red"),
                ("今日盈亏", money(stats.get("daily_pnl", account.get("daily_pnl", 0))), "green" if float(stats.get("daily_pnl", account.get("daily_pnl", 0)) or 0) >= 0 else "red"),
                ("当前浮盈", money(stats.get("current_unrealized_pnl", account.get("unrealized_pnl", 0))), "green" if float(stats.get("current_unrealized_pnl", 0) or 0) >= 0 else "red"),
                ("最大单笔盈利", money(stats.get("max_win")), "green"),
                ("最大单笔亏损", money(stats.get("max_loss")), "red"),
                ("Profit Factor", f"{float(stats.get('profit_factor', 0) or 0):.2f}", "blue"),
                ("平均R倍数", f"{float(stats.get('avg_r_multiple', 0) or 0):+.2f}R", ""),
                ("多单胜率", f"{float(stats.get('long_win_rate', 0) or 0):.2f}%", ""),
                ("空单胜率", f"{float(stats.get('short_win_rate', 0) or 0):.2f}%", ""),
                ("常见平仓原因", str(stats.get("common_close_reason", "暂无")), "yellow"),
                ("最佳交易对象", str(stats.get("best_symbol", "暂无")), "green"),
                ("最差交易对象", str(stats.get("worst_symbol", "暂无")), "red"),
                ("连续盈利", str(stats.get("consecutive_wins", 0)), "green"),
                ("连续亏损", str(stats.get("consecutive_losses", 0)), "red"),
            ]
        )
        if not history:
            st.info("暂无模拟交易历史，统计数据将在交易完成后生成。")

    with tabs[5]:
        render_sim_trade_review_panel()

    with tabs[6]:
        with st.form("sim_settings_form"):
            new_settings = dict(settings)
            new_settings["initial_balance"] = st.number_input("初始模拟资金 USDT", min_value=100.0, max_value=1000000.0, value=float(settings.get("initial_balance", 1000)), step=100.0)
            new_settings["max_position_pct"] = st.slider("单笔最大仓位比例", 1, 50, int(settings.get("max_position_pct", 10)))
            new_settings["max_risk_pct"] = st.slider("单笔最大风险比例", 1, 10, int(settings.get("max_risk_pct", 1)))
            new_settings["max_positions"] = st.slider("最大同时持仓数量", 1, 10, int(settings.get("max_positions", 3)))
            new_settings["max_same_symbol_positions"] = st.slider("同一交易对象最大持仓数量", 1, 3, int(settings.get("max_same_symbol_positions", 1)))
            new_settings["allow_long"] = st.checkbox("允许模拟做多", value=bool(settings.get("allow_long", True)))
            new_settings["allow_short"] = st.checkbox("允许模拟做空", value=bool(settings.get("allow_short", True)))
            st.info("模拟交易已锁定为 U本位永续合约模拟，杠杆固定 5x，并保持自动持续运行。")
            new_settings["market_type"] = "futures"
            new_settings["futures_leverage_locked"] = True
            new_settings["continuous_run"] = True
            new_settings["ignore_loss_limits"] = True
            new_settings["leverage"] = 5
            new_settings["entry_mode"] = "立即按当前价模拟开仓"
            new_settings["mode"] = "auto"
            new_settings["tp1_close_pct"] = st.slider("止盈1平仓比例", 10, 90, int(settings.get("tp1_close_pct", 50)))
            new_settings["move_sl_to_breakeven"] = st.checkbox("止盈1后移动止损到保本", value=bool(settings.get("move_sl_to_breakeven", True)))
            new_settings["daily_loss_limit_pct"] = st.slider("每日最大亏损限制", 1, 20, int(settings.get("daily_loss_limit_pct", 3)))
            new_settings["max_drawdown_limit_pct"] = st.slider("最大回撤限制", 1, 30, int(settings.get("max_drawdown_limit_pct", 8)))
            new_settings["consecutive_loss_pause"] = st.slider("连续亏损暂停次数", 1, 10, int(settings.get("consecutive_loss_pause", 3)))
            new_settings["signal_ttl_minutes"] = st.slider("信号有效期分钟", 5, 240, int(settings.get("signal_ttl_minutes", 60)))
            new_settings["cooldown_minutes"] = st.slider("新开仓冷却时间分钟", 1, 120, int(settings.get("cooldown_minutes", 15)))
            if st.form_submit_button("保存模拟交易参数", use_container_width=True):
                save_settings(new_settings)
                st.success("模拟交易参数已保存。")
                st.rerun()

    with tabs[7]:
        if not events:
            st.info("当前暂无模拟事件。")
        for event in events[:80]:
            st.caption(f"{event.get('time')}｜{event.get('event_type')}｜{event.get('symbol')}｜{event.get('content') or event.get('reason')}")


def render_positions() -> None:
    """持仓页。"""
    render_page_head("positions")
    summary = refresh_sim_positions_lightweight(get_sim_account_summary())
    account = summary.get("account") or {}
    positions = summary.get("positions") or []
    orders = summary.get("orders") or []
    history = summary.get("history") or []
    live_records = load_live_order_records(50)
    symbols = [row.get("symbol") for row in positions + orders + history + live_records if row.get("symbol")]
    render_kline_jump_links(symbols, "持仓与订单相关K线")
    render_metric_grid(
        [
            ("模拟持仓", str(len([p for p in positions if p.get("status") in {"open", "partially_closed"}])), "yellow"),
            ("待触发模拟订单", str(len([o for o in orders if o.get("status") == "pending"])), "blue"),
            ("模拟历史订单", str(len(history)), ""),
            ("真实订单记录", str(len(live_records)), "yellow"),
            ("模拟权益", f"{float(account.get('equity', 0) or 0):,.2f} USDT", "green"),
            ("模拟状态", str(account.get("status", "stopped")), "green" if account.get("status") == "running" else "yellow"),
        ]
    )
    st.markdown(
        f"""
        <div class="app-shell"><div class="module-grid">
          <div class="module-card"><div class="module-title">当前模拟持仓</div>
            <div class="module-desc">详细平仓、止损到保本等操作在交易页完成。</div>
            {"".join(f'<div class="status-card" style="margin-top:6px;">{kline_symbol_link(pos.get("symbol"), str(pos.get("symbol")))}｜{escape(str(pos.get("direction", "-")))}｜{escape(str(pos.get("status", "-")))}｜数量 {float(pos.get("quantity", 0) or 0):.6f}<br>入场 {format_waiting_price(pos.get("entry_price"))}｜当前 {format_waiting_price(pos.get("current_price"))}｜浮盈 {float(pos.get("unrealized_pnl", 0) or 0):+.4f} USDT / {float(pos.get("unrealized_pnl_pct", 0) or 0):+.2f}%<br>价格状态 {escape(str(pos.get("price_status", "missing")))}｜最后价格更新时间 {escape(str(pos.get("last_price_update") or pos.get("update_time") or "等待刷新"))}</div>' for pos in positions[:20]) or '<div class="status-card" style="margin-top:6px;">当前暂无模拟持仓。</div>'}
          </div>
          <div class="module-card"><div class="module-title">待触发模拟订单</div>
            {"".join(f'<div class="status-card" style="margin-top:6px;">{kline_symbol_link(order.get("symbol"), str(order.get("symbol")))}｜{escape(str(order.get("direction", "-")))}｜{escape(str(order.get("action", "-")))}｜入场区 {format_price(order.get("entry_zone_low"))} - {format_price(order.get("entry_zone_high"))}</div>' for order in orders[:20]) or '<div class="status-card" style="margin-top:6px;">当前暂无待触发模拟订单。</div>'}
          </div>
          <div class="module-card"><div class="module-title">最近模拟完成订单</div>
            {"".join(f'<div class="status-card" style="margin-top:6px;">{kline_symbol_link(row.get("symbol"), str(row.get("symbol")))}｜{escape(str(row.get("close_reason", "-")))}｜盈亏 {float(row.get("pnl", 0) or 0):+.2f} USDT</div>' for row in history[:20]) or '<div class="status-card" style="margin-top:6px;">当前暂无完成订单。</div>'}
          </div>
          <div class="module-card"><div class="module-title">最近真实订单</div>
            {"".join(f'<div class="status-card" style="margin-top:6px;">{kline_symbol_link(row.get("symbol"), str(row.get("symbol")))}｜{escape(str(row.get("side", "-")))}｜{escape(str(row.get("order_status", "-")))}｜{escape(str(row.get("order_id", "-")))}</div>' for row in live_records[:20]) or '<div class="status-card" style="margin-top:6px;">当前暂无真实订单记录。</div>'}
          </div>
          <div class="module-card"><div class="module-title">操作入口</div>
            <div class="status-card" style="margin-top:6px;">
              <a class="watch-pill" href="?page=trade&symbol={escape(str(st.session_state.get("current_symbol", "BTCUSDT")))}" target="_self">进入交易页</a>
              <a class="watch-pill" href="?page=trade_records&symbol={escape(str(st.session_state.get("current_symbol", "BTCUSDT")))}" target="_self">交易记录</a>
              {kline_symbol_link(st.session_state.get("current_symbol", "BTCUSDT"), "当前币种K线", "watch-pill")}
            </div>
          </div>
        </div></div>
        """,
        unsafe_allow_html=True,
    )


def render_learning() -> None:
    """学习页。"""
    render_page_head("learning")
    replay = analyze_replay_learning()
    summary = replay.get("summary", {})
    win_loss = replay.get("win_loss", {})
    weaknesses = replay.get("weaknesses", [])
    members = replay.get("member_performance", [])
    suggestions = replay.get("strategy_factory_suggestions", [])
    history = replay.get("history", [])

    st.markdown(
        """
        <div class="app-shell">
          <div class="module-card">
            <div class="module-title">复盘学习中心</div>
            <div class="module-desc">基于本地模拟交易历史，分析输赢原因、策略弱点、委员表现，并为策略工厂提供优化依据。</div>
          </div>
        </div>
        """,
        unsafe_allow_html=True,
    )

    render_metric_grid(
        [
            ("复盘样本", f"{summary.get('total_trades', 0)} 笔", "blue"),
            ("胜率", f"{float(summary.get('win_rate', 0) or 0):.2f}%", "green" if float(summary.get("win_rate", 0) or 0) >= 50 else "yellow"),
            ("累计模拟盈亏", f"{float(summary.get('total_pnl', 0) or 0):+.2f} USDT", "green" if float(summary.get("total_pnl", 0) or 0) >= 0 else "red"),
            ("平均每笔盈亏", f"{float(summary.get('avg_pnl', 0) or 0):+.2f} USDT", "green" if float(summary.get("avg_pnl", 0) or 0) >= 0 else "red"),
            ("盈利 / 亏损", f"{summary.get('wins', 0)} / {summary.get('losses', 0)}", ""),
            ("人工干预", f"{summary.get('manual_override_count', 0)} 次", "yellow" if summary.get("manual_override_count", 0) else ""),
            ("最大盈利", f"{float(summary.get('best_trade', 0) or 0):+.2f} USDT", "green"),
            ("最大亏损", f"{float(summary.get('worst_trade', 0) or 0):+.2f} USDT", "red"),
            ("数据质量", str(summary.get("data_quality", "poor")), "green" if summary.get("data_quality") == "good" else "yellow" if summary.get("data_quality") == "partial" else "red"),
        ]
    )

    st.markdown(
        f"""
        <div class="app-shell"><div class="module-card">
          <div class="module-title">复盘结论</div>
          <div class="status-card">{escape(str(summary.get("sample_warning", "暂无复盘样本。")))}</div>
          <div class="status-card" style="margin-top:8px;">
            <b>盈利原因：</b>{escape(str(win_loss.get("win_summary", "暂无盈利样本。")))}<br>
            <b>亏损原因：</b>{escape(str(win_loss.get("loss_summary", "暂无亏损样本。")))}<br>
            <b>主要亏损来源：</b>{escape(str(win_loss.get("main_loss_reason", "暂无")))}
          </div>
        </div></div>
        """,
        unsafe_allow_html=True,
    )

    tabs = st.tabs(["输赢原因", "策略弱点", "委员表现", "外部AI表现", "人工干预", "策略工厂建议", "交易复盘记录"])

    with tabs[0]:
        reasons = win_loss.get("close_reason_breakdown") or []
        if not reasons:
            st.info("暂无足够交易记录，完成模拟交易后会生成输赢原因拆解。")
        for item in reasons[:12]:
            avg_pnl = float(item.get("avg_pnl", 0) or 0)
            st.markdown(
                f"""
                <div class="status-card">
                  <b>{escape(str(item.get("reason", "未知原因")))}</b><br>
                  出现次数：{item.get("count", 0)}｜亏损次数：{item.get("loss_count", 0)}｜
                  平均盈亏：<span class="{_signal_color("支持交易" if avg_pnl >= 0 else "反对交易")}">{avg_pnl:+.2f} USDT</span><br>
                  解释：该平仓原因用于判断当前模拟系统的主要收益来源或亏损来源。
                </div>
                """,
                unsafe_allow_html=True,
            )

    with tabs[1]:
        if not weaknesses:
            st.success("当前没有发现明显策略弱点。若样本较少，该结论仅作保守参考。")
        for item in weaknesses:
            st.markdown(
                f"""
                <div class="status-card">
                  <b>{escape(str(item.get("dimension", "")))}：{escape(str(item.get("name", "")))}</b><br>
                  交易数：{item.get("trade_count", 0)}｜胜率：{float(item.get("win_rate", 0) or 0):.2f}%｜
                  平均盈亏：{float(item.get("avg_pnl", 0) or 0):+.2f} USDT｜平均R：{float(item.get("avg_r", 0) or 0):+.2f}R<br>
                  {escape(str(item.get("explanation", "")))}
                </div>
                """,
                unsafe_allow_html=True,
            )

    with tabs[2]:
        if not members:
            st.info("当前历史交易中缺少委员投票快照。新平仓交易会保存委员会快照，后续可评估委员表现。")
        for member in members:
            st.markdown(
                f"""
                <div class="status-card">
                  <b>{escape(str(member.get("member_name", "未知委员")))}</b><br>
                  支持交易次数：{member.get("support_count", 0)}｜判断正确：{member.get("correct_count", 0)}｜判断错误：{member.get("wrong_count", 0)}｜否决次数：{member.get("veto_count", 0)}<br>
                  支持后胜率：{float(member.get("accuracy", 0) or 0):.2f}%｜支持后平均盈亏：{float(member.get("avg_pnl_when_supported", 0) or 0):+.2f} USDT<br>
                  {escape(str(member.get("explanation", "")))}
                </div>
                """,
                unsafe_allow_html=True,
            )

    with tabs[3]:
        perf = get_external_ai_performance_summary()
        st.markdown("DeepSeek/Gemini 当前为正式投票委员，参与委员会权重统计；仍不能直接执行交易或绕过风控。")
        for provider, label in [("deepseek", "DeepSeek"), ("gemini", "Gemini")]:
            item = perf.get(provider) or {}
            dirs = item.get("direction_counts") or {}
            st.markdown(
                f"""
                <div class="status-card">
                  <b>{label} 表现卡</b>｜升级建议：<span class="yellow">{escape(str(item.get("upgrade_suggestion", "继续影子模式")))}</span><br>
                  总分析：{item.get("total_calls", 0)}｜有效：{item.get("valid_calls", 0)}｜失败：{item.get("failed_calls", 0)}｜失败率：{float(item.get("failure_rate", 0) or 0):.2f}%｜平均耗时：{float(item.get("avg_duration_ms", 0) or 0):.0f} ms<br>
                  方向统计：多 {dirs.get("long", 0)} / 空 {dirs.get("short", 0)} / 中性 {dirs.get("neutral", 0)}｜软否决：{item.get("soft_veto_count", 0)}<br>
                  方向准确率：{float(item.get("direction_accuracy", 0) or 0):.2f}%｜风险识别有效率：{float(item.get("risk_identification_effective_rate", 0) or 0):.2f}%｜软否决有效率：{float(item.get("soft_veto_effective_rate", 0) or 0):.2f}%<br>
                  过度保守：{item.get("over_conservative_count", 0)}｜过度激进：{item.get("over_aggressive_count", 0)}｜{escape(str(item.get("sample_warning", "样本数量不足，暂不评估外部AI准确率。")))}
                </div>
                """,
                unsafe_allow_html=True,
            )

    with tabs[4]:
        overrides = replay.get("manual_overrides") or []
        if not overrides:
            st.info("暂无人工仓位干预记录。")
        for row in overrides[:50]:
            st.markdown(
                f"""
                <div class="status-card">
                  <b>{escape(str(row.get("symbol", "")))}</b>｜{escape(str(row.get("mode", "模拟")))}｜{escape(str(row.get("result", "")))}<br>
                  系统建议：{escape(str(row.get("system_position_suggestion", "")))}｜风控最大：{escape(str(row.get("risk_max_position", "")))}%｜用户选择：{escape(str(row.get("user_selected_position", "")))}%<br>
                  委员会动作：{escape(str(row.get("committee_final_action", "")))}｜风险评分：{escape(str(row.get("risk_score", "")))}｜原因：{escape(str(row.get("reason", "")))}<br>
                  复盘标记：该记录存在人工仓位干预，需要单独评估系统原始建议与用户干预后表现。
                </div>
                """,
                unsafe_allow_html=True,
            )

    with tabs[5]:
        for item in suggestions:
            priority = str(item.get("priority", "中"))
            color = "red" if priority == "高" else "yellow"
            st.markdown(
                f"""
                <div class="status-card">
                  <b>{escape(str(item.get("title", "")))}</b>｜<span class="{color}">优先级：{escape(priority)}</span><br>
                  {escape(str(item.get("suggestion", "")))}
                </div>
                """,
                unsafe_allow_html=True,
            )

    with tabs[6]:
        if not history:
            st.info("暂无模拟交易历史。请先在交易页运行模拟交易，完成平仓后这里会自动生成复盘记录。")
        for row in history[:50]:
            pnl = float(row.get("pnl", 0) or 0)
            st.markdown(
                f"""
                <div class="status-card">
                  <b>{escape(str(row.get("symbol", "")))}</b>｜{escape("空单" if row.get("direction") == "short" else "多单")}｜{escape(str(row.get("close_reason", "")))}｜
                  <span class="{_signal_color("支持交易" if pnl >= 0 else "反对交易")}">{pnl:+.2f} USDT</span><br>
                  委员会动作：{escape(str(row.get("committee_action", "暂无")))}｜策略：{escape(str(row.get("strategy_name", "暂无")))}｜风险收益比：{escape(str(row.get("risk_reward_ratio", "暂无")))}<br>
                  主席总结：{escape(_safe_committee_text(row.get("chairman_summary", "暂无"), 420))}
                </div>
                """,
                unsafe_allow_html=True,
            )


def render_strategy_factory() -> None:
    """策略工厂 + 回测中心。"""
    render_page_head("strategy")
    strategies = get_available_strategies()
    results = load_backtest_results()
    candidates = get_strategy_candidates()
    replay_hints = get_replay_optimization_hints()
    st.markdown(
        """
        <div class="app-shell">
          <div class="module-card warning-box">
            <b>策略研究安全提示</b><br>
            当前为策略研究与历史回测，不会执行真实订单。历史回测结果不代表未来收益，候选策略只能进入模拟验证。
          </div>
        </div>
        """,
        unsafe_allow_html=True,
    )
    tabs = st.tabs(["策略库", "策略配置", "回测中心", "回测结果", "参数优化", "策略对比", "候选策略", "复盘建议"])

    strategy_names = {s["strategy_id"]: s["strategy_name"] for s in strategies}
    default_strategy_id = strategies[0]["strategy_id"] if strategies else ""

    with tabs[0]:
        if not strategies:
            st.warning("策略库暂不可用。")
        for strategy in strategies:
            st.markdown(
                f"""
                <div class="status-card">
                  <b>{escape(str(strategy.get("strategy_name")))}</b>｜{escape(str(strategy.get("strategy_type")))}｜风险：{escape(str(strategy.get("risk_profile")))}<br>
                  支持周期：{escape(" / ".join(strategy.get("supported_timeframes", [])))}｜市场：{escape(" / ".join(strategy.get("supported_markets", [])))}<br>
                  {escape(str(strategy.get("description", "")))}
                </div>
                """,
                unsafe_allow_html=True,
            )

    with tabs[1]:
        strategy_id = st.selectbox("选择策略", list(strategy_names.keys()), format_func=lambda sid: strategy_names.get(sid, sid), key="factory_config_strategy") if strategies else default_strategy_id
        config = get_strategy_config(strategy_id) if strategy_id else {}
        with st.form("strategy_config_form"):
            st.caption("参数修改只保存为策略配置版本，不会自动修改生产策略。")
            new_config: dict[str, Any] = {}
            for key, value in config.items():
                label = key.replace("_", " ")
                if isinstance(value, bool):
                    new_config[key] = st.checkbox(label, value=value)
                elif isinstance(value, int):
                    new_config[key] = st.number_input(label, value=int(value), step=1)
                elif isinstance(value, float):
                    new_config[key] = st.number_input(label, value=float(value), step=0.1)
                else:
                    new_config[key] = st.text_input(label, value=str(value))
            c1, c2 = st.columns(2)
            if c1.form_submit_button("保存策略配置", use_container_width=True):
                save_strategy_config(strategy_id, new_config)
                st.success("策略配置已保存，仅用于回测和候选策略。")
                st.rerun()
            if c2.form_submit_button("重置默认参数", use_container_width=True):
                reset_strategy_config(strategy_id)
                st.success("已恢复默认参数。")
                st.rerun()

    with tabs[2]:
        with st.form("backtest_form"):
            c1, c2 = st.columns(2)
            bt_strategy = c1.selectbox("策略", list(strategy_names.keys()), format_func=lambda sid: strategy_names.get(sid, sid), key="bt_strategy") if strategies else default_strategy_id
            symbol = c2.selectbox("交易对象", FALLBACK_SYMBOLS, index=0)
            c3, c4, c5 = st.columns(3)
            timeframe = c3.selectbox("周期", ["5m", "15m", "1h", "4h", "1d"], index=1)
            period_days = c4.selectbox("回测范围", [7, 30, 90, 180, 365], index=1, format_func=lambda d: f"最近{d}天")
            initial_balance = c5.number_input("初始资金 USDT", min_value=100.0, max_value=1000000.0, value=1000.0, step=100.0)
            c6, c7, c8 = st.columns(3)
            position_pct = c6.slider("单笔仓位比例", 1, 50, 10)
            fee_rate = c7.number_input("手续费率", min_value=0.0, max_value=0.01, value=0.0004, step=0.0001, format="%.4f")
            slippage = c8.number_input("滑点", min_value=0.0, max_value=0.01, value=0.0002, step=0.0001, format="%.4f")
            allow_long = st.checkbox("允许做多", value=True)
            allow_short = st.checkbox("允许做空", value=True)
            run_clicked = st.form_submit_button("运行回测", use_container_width=True)
        if run_clicked:
            try:
                result = run_backtest(
                    bt_strategy,
                    get_strategy_config(bt_strategy),
                    symbol,
                    timeframe,
                    int(period_days),
                    {"initial_balance": initial_balance, "position_pct": position_pct, "fee_rate": fee_rate, "slippage": slippage, "allow_long": allow_long, "allow_short": allow_short},
                )
                st.session_state["latest_backtest_result"] = result
                st.success("回测完成，已保存结果。")
            except Exception as exc:
                st.error(f"回测运行失败：{exc}")

        latest = st.session_state.get("latest_backtest_result")
        if latest:
            m = latest.get("metrics", {})
            render_metric_grid(
                [
                    ("策略评级", latest.get("grade", "E"), "green" if latest.get("grade") in {"A", "B"} else "yellow"),
                    ("交易次数", str(m.get("total_trades", 0)), ""),
                    ("总收益率", f"{float(m.get('return_pct', 0) or 0):+.2f}%", "green" if float(m.get("return_pct", 0) or 0) >= 0 else "red"),
                    ("最大回撤", f"{float(m.get('max_drawdown_pct', 0) or 0):.2f}%", "yellow"),
                    ("胜率", f"{float(m.get('win_rate', 0) or 0):.2f}%", ""),
                    ("Profit Factor", f"{float(m.get('profit_factor', 0) or 0):.2f}", "blue"),
                    ("平均R", f"{float(m.get('avg_r', 0) or 0):+.2f}R", ""),
                    ("过拟合风险", (latest.get("overfit_risk") or {}).get("level", "高"), "red" if (latest.get("overfit_risk") or {}).get("level") == "高" else "yellow"),
                ]
            )
            if latest.get("equity_curve"):
                st.line_chart({"权益": [float(p.get("equity", 0) or 0) for p in latest["equity_curve"]]})
            for reason in (latest.get("overfit_risk") or {}).get("reasons", []):
                st.warning(reason)

    with tabs[3]:
        if not results:
            st.info("暂无回测结果。请先在回测中心运行一次回测。")
        for result in results[:20]:
            m = result.get("metrics") or {}
            st.markdown(
                f"""
                <div class="status-card">
                  <b>{escape(str(result.get("strategy_name")))}</b>｜{escape(str(result.get("symbol")))}｜{escape(str(result.get("timeframe")))}｜评级：{escape(str(result.get("grade", "E")))}<br>
                  收益率：{float(m.get("return_pct", 0) or 0):+.2f}%｜最大回撤：{float(m.get("max_drawdown_pct", 0) or 0):.2f}%｜胜率：{float(m.get("win_rate", 0) or 0):.2f}%｜PF：{float(m.get("profit_factor", 0) or 0):.2f}<br>
                  过拟合风险：{escape(str((result.get("overfit_risk") or {}).get("level", "高")))}｜创建时间：{escape(str(result.get("created_time", "")))}
                </div>
                """,
                unsafe_allow_html=True,
            )
            c1, c2 = st.columns(2)
            if c1.button("加入候选策略", key=f"candidate_{result.get('result_id')}", use_container_width=True):
                create_strategy_candidate(result)
                st.success("已加入候选策略库，仅用于模拟验证。")
                st.rerun()
            if c2.button("导出Markdown报告", key=f"report_{result.get('result_id')}", use_container_width=True):
                path = export_strategy_report(result)
                st.success(f"报告已导出：{path}")

    with tabs[4]:
        st.caption("参数优化可能产生过拟合，必须结合样本外测试和模拟交易验证。")
        with st.form("optimizer_form"):
            opt_strategy = st.selectbox("优化策略", list(strategy_names.keys()), format_func=lambda sid: strategy_names.get(sid, sid), key="opt_strategy") if strategies else default_strategy_id
            opt_symbol = st.selectbox("优化交易对象", FALLBACK_SYMBOLS, index=0, key="opt_symbol")
            opt_tf = st.selectbox("优化周期", ["5m", "15m", "1h"], index=1, key="opt_tf")
            opt_days = st.selectbox("优化范围", [7, 30, 90], index=1, format_func=lambda d: f"最近{d}天", key="opt_days")
            st.caption("默认扫描 ATR倍数 与 最小风险收益比。组合过多会变慢。")
            if st.form_submit_button("运行参数网格搜索", use_container_width=True):
                base = get_strategy_config(opt_strategy)
                grid = {"atr_mult": [1.0, 1.3, 1.6, 2.0], "rr_min": [1.1, 1.3, 1.5]}
                try:
                    st.session_state["optimizer_results"] = run_parameter_grid_search(opt_strategy, base, opt_symbol, opt_tf, int(opt_days), grid, limit=12)
                    st.success("参数优化完成。")
                except Exception as exc:
                    st.error(f"参数优化失败：{exc}")
        for row in st.session_state.get("optimizer_results", [])[:20]:
            m = row.get("metrics") or {}
            st.markdown(f"参数：`{row.get('config')}`｜收益率 {float(m.get('return_pct',0) or 0):+.2f}%｜回撤 {float(m.get('max_drawdown_pct',0) or 0):.2f}%｜PF {float(m.get('profit_factor',0) or 0):.2f}｜评级 {row.get('grade')}｜过拟合 {(row.get('overfit_risk') or {}).get('level')}")

    with tabs[5]:
        compare_rows = compare_strategy_results(results[:8])
        if not compare_rows:
            st.info("暂无可对比回测结果。")
        for row in compare_rows:
            st.markdown(f"**{row['策略']}**｜{row['交易对象']} {row['周期']}｜收益率 {row['收益率']:+.2f}%｜回撤 {row['最大回撤']:.2f}%｜胜率 {row['胜率']:.2f}%｜PF {row['Profit Factor']:.2f}｜评级 {row['评级']}｜过拟合 {row['过拟合']}")

    with tabs[6]:
        if not candidates:
            st.info("暂无候选策略。达到条件的回测结果可手动加入候选库。")
        for candidate in candidates:
            st.markdown(
                f"""
                <div class="status-card">
                  <b>{escape(str(candidate.get("strategy_name")))}</b>｜评级：{escape(str(candidate.get("grade")))}｜状态：{escape(str(candidate.get("status", "待模拟验证")))}<br>
                  交易对象：{escape(" / ".join(candidate.get("symbols", [])))}｜周期：{escape(" / ".join(candidate.get("timeframes", [])))}<br>
                  收益率：{float(candidate.get("total_return", 0) or 0):+.2f}%｜回撤：{float(candidate.get("max_drawdown", 0) or 0):.2f}%｜胜率：{float(candidate.get("win_rate", 0) or 0):.2f}%｜PF：{float(candidate.get("profit_factor", 0) or 0):.2f}<br>
                  过拟合风险：{escape(str(candidate.get("overfit_risk", "高")))}｜说明：候选策略只能进入模拟验证，不能直接实盘。
                </div>
                """,
                unsafe_allow_html=True,
            )

    with tabs[7]:
        summary = replay_hints.get("summary", {})
        st.markdown(f"复盘样本：{summary.get('total_trades', 0)} 笔｜数据质量：{summary.get('data_quality', 'poor')}｜{summary.get('sample_warning', '')}")
        for item in replay_hints.get("suggestions", []):
            st.markdown(f"**{item.get('title')}**｜优先级：{item.get('priority')}  \n{item.get('suggestion')}")


def render_live_trading_center() -> None:
    """实盘交易中心前置安全版。"""
    render_page_head("live")
    status = get_live_safety_status()
    settings = status.get("settings") or load_live_settings()
    credentials = status.get("credentials") or {}
    connection = status.get("connection") or {}
    permission = status.get("permission") or {}
    withdraw = status.get("withdraw") or {}
    ip_status = status.get("ip_status") or {}
    st.markdown(
        """
        <div class="app-shell">
          <div class="module-card warning-box">
            <b>实盘前置安全提示</b><br>
            当前为小资金手动实盘执行功能。真实订单会使用真实资金，请谨慎操作。系统不会自动下单，任何真实订单都必须由你手动确认。
          </div>
        </div>
        """,
        unsafe_allow_html=True,
    )
    render_metric_grid(
        [
            ("当前模式", str(settings.get("mode", "read_only")), "yellow"),
            ("API连接", str(connection.get("status", "未检查")), "green" if connection.get("ok") else "red"),
            ("权限状态", str(permission.get("permission_status", "未配置")), "yellow"),
            ("提现权限", str(withdraw.get("status", "未知")), "red" if withdraw.get("status") == "高危开启" else "green"),
            ("IP白名单", str(ip_status.get("status", "未确认")), "green" if ip_status.get("ok") else "yellow"),
            ("安全锁", "已开启" if settings.get("kill_switch_enabled") else "未开启", "red" if settings.get("kill_switch_enabled") else "green"),
            ("Live Manual", "已启用" if settings.get("live_manual_enabled") else "默认禁用", "red" if settings.get("live_manual_enabled") else "yellow"),
            ("实盘候选", "允许审查" if status.get("allow_live_candidate") else "暂不允许", "green" if status.get("allow_live_candidate") else "red"),
        ]
    )

    tabs = st.tabs(["安全总览", "API设置", "账户只读", "订单预览", "执行前检查", "Dry-run / Testnet", "小资金实盘", "实盘持仓", "自动试运行", "实盘准入", "审计日志"])

    with tabs[0]:
        st.markdown(
            f"""
            <div class="app-shell"><div class="module-card">
              <div class="module-title">安全总览</div>
              <div class="status-card">
                安全说明：{escape(str(status.get("safety_notice", "")))}<br>
                API Key：{escape(str(credentials.get("masked_api_key", "未配置")))}｜Secret：{escape(str(credentials.get("secret_status", "未配置")))}<br>
                API连接：{escape(str(connection.get("message", "")))}<br>
                权限检查：{escape(str(permission.get("message", "")))}<br>
                提现权限：{escape(str(withdraw.get("message", "")))}<br>
                IP限制：{escape(str(ip_status.get("message", "")))}<br>
                安全锁原因：{escape(str(settings.get("kill_switch_reason", "") or "无"))}
              </div>
            </div></div>
            """,
            unsafe_allow_html=True,
        )
        c1, c2 = st.columns(2)
        if c1.button("触发实盘安全锁", use_container_width=True):
            trigger_live_kill_switch("用户在实盘安全中心手动触发。")
            st.rerun()
        confirm_release = c2.checkbox("我确认解除实盘安全锁，并理解风险")
        if c2.button("解除安全锁", disabled=not confirm_release, use_container_width=True):
            release_live_kill_switch("用户二次确认解除安全锁。")
            st.rerun()

    with tabs[1]:
        st.markdown(
            f"""
            <div class="app-shell"><div class="module-card">
              <div class="module-title">API密钥安全管理</div>
              <div class="status-card">
                API Key：{escape(str(credentials.get("masked_api_key", "未配置")))}<br>
                API Secret：已隐藏，页面不会显示，也不会写入日志。<br>
                来源：{escape(str(credentials.get("source", "未配置")))}<br>
                请不要把 API Secret 发送给任何人，也不要上传到任何公共仓库。
              </div>
            </div></div>
            """,
            unsafe_allow_html=True,
        )
        with st.form("live_settings_form"):
            new_settings = dict(settings)
            new_settings["mode"] = st.selectbox("交易模式", ["read_only", "dry_run", "testnet", "live_manual"], index=["read_only", "dry_run", "testnet", "live_manual"].index(str(settings.get("mode", "read_only"))) if str(settings.get("mode", "read_only")) in ["read_only", "dry_run", "testnet", "live_manual"] else 0, format_func=lambda x: {"read_only": "只读模式", "dry_run": "Dry-run 本地预演", "testnet": "Testnet 测试网", "live_manual": "Live Manual 小资金手动"}[x])
            new_settings["market_type"] = st.selectbox("市场类型", ["spot", "futures"], index=0 if settings.get("market_type") == "spot" else 1, format_func=lambda x: "现货" if x == "spot" else "U本位合约")
            new_settings["live_manual_enabled"] = st.checkbox("手动开启 Live Manual 小资金实盘流程（仍需 LIVE_TRADING_ENABLED=true）", value=bool(settings.get("live_manual_enabled")))
            new_settings["ip_whitelist_confirmed"] = st.checkbox("我已开启 IP 白名单，或已理解未开启的风险", value=bool(settings.get("ip_whitelist_confirmed")))
            new_settings["max_live_notional_usdt"] = st.number_input("单笔真实名义金额上限 USDT", min_value=1.0, max_value=50.0, value=float(settings.get("max_live_notional_usdt", 10)), step=1.0)
            new_settings["daily_live_notional_limit_usdt"] = st.number_input("单日真实交易总额上限 USDT", min_value=10.0, max_value=500.0, value=float(settings.get("daily_live_notional_limit_usdt", 100)), step=10.0)
            new_settings["daily_live_loss_limit_usdt"] = st.number_input("单日真实亏损上限 USDT", min_value=1.0, max_value=100.0, value=float(settings.get("daily_live_loss_limit_usdt", 5)), step=1.0)
            new_settings["max_live_risk_pct"] = st.number_input("单笔真实风险上限 %", min_value=0.1, max_value=5.0, value=float(settings.get("max_live_risk_pct", 0.5)), step=0.1)
            symbols_text = st.text_input("允许实盘候选交易对象", value=",".join(settings.get("allowed_symbols", ["BTCUSDT", "ETHUSDT"])))
            new_settings["allowed_symbols"] = [s.strip().upper() for s in symbols_text.split(",") if s.strip()]
            if st.form_submit_button("保存安全配置", use_container_width=True):
                save_live_settings(new_settings)
                st.success("安全配置已保存。真实提交仍要求 LIVE_TRADING_ENABLED=true、Spot Test Order 和确认短句。")
                st.rerun()

    with tabs[2]:
        snapshot = get_live_account_snapshot(settings.get("mode") == "testnet", settings.get("market_type", "spot"))
        label = snapshot.get("label", "真实账户只读数据")
        st.markdown(f"**{label}**｜更新时间：{snapshot.get('updated_time')}")
        if not snapshot.get("ok"):
            st.warning(snapshot.get("message"))
        balances = snapshot.get("balances") or []
        if not balances:
            st.info("当前暂无可显示余额，或 API 未配置。")
        for row in balances[:30]:
            st.markdown(f"{row.get('asset')}｜可用：{row.get('free')}｜锁定/钱包：{row.get('locked', row.get('wallet', '0'))}")
        positions = snapshot.get("positions") or []
        if positions:
            st.markdown("**真实持仓只读显示**")
            for pos in positions[:20]:
                st.caption(f"{pos.get('symbol')}｜数量：{pos.get('positionAmt')}｜未实现盈亏：{pos.get('unrealizedProfit')}")

    with tabs[3]:
        ticker = market_cache.get_ticker(st.session_state.get("current_symbol", "BTCUSDT")) or {}
        default_price = float(ticker.get("last_price") or 0)
        with st.form("live_preview_form"):
            p1, p2 = st.columns(2)
            plan_symbol = p1.text_input("交易对象", value=st.session_state.get("current_symbol", "BTCUSDT")).upper()
            side = p2.selectbox("方向", ["buy", "sell"], format_func=lambda x: "买入 / 做多" if x == "buy" else "卖出 / 做空")
            p3, p4 = st.columns(2)
            price = p3.number_input("计划价格", min_value=0.0, value=float(default_price or 0), step=0.01)
            quantity = p4.number_input("计划数量", min_value=0.0, value=0.001, step=0.001, format="%.6f")
            p5, p6 = st.columns(2)
            stop_loss = p5.number_input("止损价", min_value=0.0, value=0.0, step=0.01)
            take_profit = p6.number_input("止盈价", min_value=0.0, value=0.0, step=0.01)
            preview_clicked = st.form_submit_button("生成订单预览", use_container_width=True)
        if preview_clicked:
            preview = create_live_order_preview({"symbol": plan_symbol, "side": side, "price": price, "quantity": quantity, "stop_loss": stop_loss, "take_profit": take_profit, "market_type": settings.get("market_type")})
            st.session_state["live_order_plan"] = preview.get("plan")
            st.session_state["live_order_preview"] = preview
        preview = st.session_state.get("live_order_preview")
        if preview:
            plan = preview.get("plan", {})
            render_metric_grid(
                [
                    ("预览状态", "通过" if preview.get("ok") else "失败", "green" if preview.get("ok") else "red"),
                    ("名义金额", f"{float(plan.get('notional', 0) or 0):.2f} USDT", "yellow"),
                    ("预计手续费", f"{float(plan.get('estimated_fee', 0) or 0):.4f} USDT", ""),
                    ("预计滑点", f"{float(plan.get('estimated_slippage', 0) or 0):.4f} USDT", ""),
                    ("最大亏损", f"{float(plan.get('max_loss', 0) or 0):.4f} USDT", "red"),
                    ("风险收益比", f"{float(plan.get('risk_reward_ratio', 0) or 0):.2f}", ""),
                    ("交易规则", "通过" if preview.get("rule_check", {}).get("ok") else "失败", "green" if preview.get("rule_check", {}).get("ok") else "red"),
                    ("执行说明", "仅预览，不下单", "yellow"),
                ]
            )
            for err in preview.get("rule_check", {}).get("errors", []) + preview.get("risk_errors", []):
                st.error(err)
            for warn in preview.get("rule_check", {}).get("warnings", []):
                st.warning(warn)

    with tabs[4]:
        plan = st.session_state.get("live_order_plan") or {"symbol": st.session_state.get("current_symbol", "BTCUSDT"), "price": float((market_cache.get_ticker(st.session_state.get("current_symbol", "BTCUSDT")) or {}).get("last_price") or 0), "quantity": 0.001, "side": "buy", "market_type": settings.get("market_type")}
        confirmed = st.checkbox("我确认这只是执行前检查，不代表真实下单")
        if st.button("运行执行前检查清单", use_container_width=True):
            st.session_state["live_preflight"] = run_live_preflight_check(plan, user_confirmed=confirmed)
        preflight = st.session_state.get("live_preflight")
        if preflight:
            st.markdown(f"**检查结果：{preflight.get('message')}**")
            for item in preflight.get("checklist", []):
                if item.get("status") == "通过":
                    st.success(f"{item.get('name')}：{item.get('message')}")
                elif item.get("status") == "警告":
                    st.warning(f"{item.get('name')}：{item.get('message')}")
                else:
                    st.error(f"{item.get('name')}：{item.get('message')}")

    with tabs[5]:
        plan = st.session_state.get("live_order_plan") or {"symbol": st.session_state.get("current_symbol", "BTCUSDT"), "price": float((market_cache.get_ticker(st.session_state.get("current_symbol", "BTCUSDT")) or {}).get("last_price") or 0), "quantity": 0.001, "side": "buy", "market_type": settings.get("market_type")}
        c1, c2 = st.columns(2)
        if c1.button("运行 Dry-run 验证", use_container_width=True):
            st.session_state["dry_run_result"] = run_test_order_validation(plan)
        if c2.button("运行 Testnet 预检", use_container_width=True):
            st.session_state["dry_run_result"] = run_testnet_order_flow(plan)
        result = st.session_state.get("dry_run_result")
        if result:
            if result.get("ok"):
                st.success(result.get("message"))
            else:
                st.error(result.get("message"))

    with tabs[6]:
        exec_status = get_live_manual_execution_status()
        st.markdown(
            """
            <div class="app-shell"><div class="module-card warning-box">
              <b>小资金手动实盘风险提示</b><br>
              当前功能可能提交真实 Spot 订单，真实订单会使用真实资金。系统不会自动下单，任何真实订单都必须由你手动确认并输入确认短句。
            </div></div>
            """,
            unsafe_allow_html=True,
        )
        render_metric_grid(
            [
                ("LIVE_TRADING_ENABLED", "true" if exec_status.get("live_trading_enabled") else "false", "green" if exec_status.get("live_trading_enabled") else "red"),
                ("Live Manual", "已开启" if exec_status.get("live_manual_enabled") else "未开启", "green" if exec_status.get("live_manual_enabled") else "red"),
                ("当前模式", str(exec_status.get("mode", "read_only")), "yellow"),
                ("安全锁", "已开启" if exec_status.get("kill_switch_enabled") else "未开启", "red" if exec_status.get("kill_switch_enabled") else "green"),
                ("单笔上限", f"{float(exec_status.get('max_live_notional_usdt', 0) or 0):.2f} USDT", "yellow"),
                ("硬上限", f"{float(exec_status.get('hard_max_live_notional_usdt', 0) or 0):.2f} USDT", "red"),
                ("今日已用", f"{float(exec_status.get('daily_live_notional', 0) or 0):.2f} USDT", ""),
                ("今日上限", f"{float(exec_status.get('daily_limit', 0) or 0):.2f} USDT", ""),
            ]
        )
        ticker = market_cache.get_ticker(st.session_state.get("current_symbol", "BTCUSDT")) or {}
        current_price = float(ticker.get("last_price") or 0)
        decision = build_current_committee_decision(st.session_state.get("current_symbol", "BTCUSDT"), ticker)
        external_ai = decision.get("external_ai") or {}
        with st.form("live_manual_plan_form"):
            c1, c2 = st.columns(2)
            symbol = c1.text_input("交易对象", value=st.session_state.get("current_symbol", "BTCUSDT")).upper()
            side = c2.selectbox("买卖方向", ["BUY", "SELL"], format_func=lambda x: "买入 BUY" if x == "BUY" else "卖出 SELL")
            c0a, c0b = st.columns(2)
            market_type = c0a.selectbox("市场类型", ["spot", "futures"], index=0 if settings.get("market_type", "spot") == "spot" else 1, format_func=lambda x: "现货 Spot" if x == "spot" else "U本位永续合约")
            leverage = c0b.slider("合约杠杆", 1, 125, int(settings.get("max_leverage", 5) or 5), help="现货订单会忽略杠杆；合约订单提交前会同步 Binance 杠杆。")
            c3, c4 = st.columns(2)
            order_type = c3.selectbox("订单类型", ["LIMIT", "MARKET"], format_func=lambda x: "限价 LIMIT" if x == "LIMIT" else "市价 MARKET（需额外谨慎）")
            price = c4.number_input("计划价格", min_value=0.0, value=float(current_price or 0), step=0.01)
            c5, c6 = st.columns(2)
            quote_amount = c5.number_input("用户选择金额 USDT", min_value=0.0, max_value=50.0, value=min(float(settings.get("system_suggested_live_amount_usdt", 5) or 5), float(settings.get("max_live_notional_usdt", 10) or 10)), step=1.0)
            default_qty = quote_amount * (leverage if market_type == "futures" else 1) / price if price else 0.0
            quantity = c6.number_input("计划数量", min_value=0.0, value=default_qty, step=0.000001, format="%.8f")
            source = st.selectbox("订单来源", ["AI交易委员会", "手动订单"], index=0)
            submitted_plan = st.form_submit_button("生成小资金订单计划", use_container_width=True)
        if submitted_plan:
            signal = committee_decision_to_sim_signal(decision) if source == "AI交易委员会" else {"source": "手动订单", "symbol": symbol}
            plan = create_live_order_plan(signal, {"symbol": symbol, "market_type": market_type, "leverage": leverage, "side": side, "order_type": order_type, "price": price, "quantity": quantity, "quote_amount": quote_amount, "source": source})
            st.session_state["live_manual_plan"] = plan
            st.session_state.pop("live_manual_test_order", None)
            st.session_state.pop("live_manual_preflight", None)
            st.success("订单计划已生成。")

        plan = st.session_state.get("live_manual_plan")
        if plan:
            validation = validate_live_order_plan(plan)
            preview = create_live_order_preview(plan)
            st.session_state["live_manual_preview"] = preview
            render_metric_grid(
                [
                    ("计划状态", str(plan.get("status", "draft")), "yellow"),
                    ("交易对象", str(plan.get("symbol", "-")), ""),
                    ("市场", "U本位合约" if plan.get("market_type") == "futures" else "现货", "blue"),
                    ("杠杆", f"{int(plan.get('leverage', 1) or 1)}x", "yellow" if plan.get("market_type") == "futures" else ""),
                    ("方向", str(plan.get("side", "-")), "green" if plan.get("side") == "BUY" else "red"),
                    ("类型", str(plan.get("order_type", "-")), ""),
                    ("用户金额", f"{float(plan.get('user_selected_amount', 0) or 0):.2f} USDT", "yellow"),
                    ("系统建议", f"{float(plan.get('system_suggested_amount', 0) or 0):.2f} USDT", ""),
                    ("风控最大", f"{float(plan.get('risk_max_amount', 0) or 0):.2f} USDT", "red"),
                    ("人工干预", "是" if plan.get("manual_override") else "否", "yellow" if plan.get("manual_override") else "green"),
                ]
            )
            if plan.get("manual_override"):
                st.warning("你选择的真实订单金额高于系统建议金额。该行为将被记录为人工干预，并在复盘中单独标记。")
            for row_name, airow in [("DeepSeek", external_ai.get("deepseek") or {}), ("Gemini", external_ai.get("gemini") or {})]:
                st.caption(f"{row_name}影子意见：{airow.get('vote', '观望')}｜风险 {airow.get('risk_level', '中')}｜建议 {airow.get('suggested_adjustment', '不调整')}｜{_safe_committee_text(airow.get('summary', '暂无'), 260)}")
            if any((external_ai.get(k) or {}).get("soft_veto") for k in ["deepseek", "gemini"]):
                st.warning("外部AI存在风险提醒，继续操作将被记录为人工风险确认。")
            if not validation.get("ok"):
                for err in validation.get("errors", []):
                    st.error(err)
            for warn in validation.get("warnings", []):
                st.warning(warn)
            st.markdown("**订单预览：这只是订单预览，尚未执行真实订单。**")
            if preview:
                p = preview.get("plan", {})
                st.markdown(f"名义金额：{float(p.get('notional',0) or 0):.2f} USDT｜预计手续费：{float(p.get('estimated_fee',0) or 0):.4f}｜滑点估算：{float(p.get('estimated_slippage',0) or 0):.4f}｜规则：{'通过' if preview.get('rule_check',{}).get('ok') else '失败'}")
            test_label = "执行 Futures Test Order 验证" if plan.get("market_type") == "futures" else "执行 Spot Test Order 验证"
            if st.button(test_label, use_container_width=True):
                st.session_state["live_manual_test_order"] = run_futures_test_order(plan) if plan.get("market_type") == "futures" else run_spot_test_order(plan)
                st.rerun()
            test_order = st.session_state.get("live_manual_test_order") or {}
            if test_order:
                (st.success if test_order.get("ok") else st.error)(test_order.get("message"))
            st.markdown("**人工确认区**")
            manual_confirmed = st.checkbox("我确认这是小资金真实订单，并理解风险", key="live_manual_confirmed")
            phrase = st.text_input("请输入确认短句：我确认执行小资金实盘订单", key="live_manual_phrase")
            if st.button("运行小资金实盘执行前检查", use_container_width=True):
                st.session_state["live_manual_preflight"] = run_live_futures_preflight(plan, test_order, manual_confirmed, phrase) if plan.get("market_type") == "futures" else run_live_manual_preflight(plan, test_order, manual_confirmed, phrase)
            preflight = st.session_state.get("live_manual_preflight") or {}
            if preflight:
                st.markdown(f"**检查结果：{preflight.get('message')}**")
                for item in preflight.get("checklist", []):
                    if item.get("status") == "通过":
                        st.success(f"{item.get('name')}：{item.get('message')}")
                    else:
                        st.error(f"{item.get('name')}：{item.get('message')}")
            can_submit = bool(preflight.get("ok"))
            submit_label = "提交真实 U本位合约小资金订单" if plan.get("market_type") == "futures" else "提交真实 Spot 小资金订单"
            if st.button(submit_label, disabled=not can_submit, use_container_width=True):
                st.session_state["live_manual_submit_result"] = submit_live_futures_order(plan, test_order, manual_confirmed, phrase) if plan.get("market_type") == "futures" else submit_live_spot_order(plan, test_order, manual_confirmed, phrase)
                st.rerun()
            submit_result = st.session_state.get("live_manual_submit_result") or {}
            if submit_result:
                (st.success if submit_result.get("ok") else st.error)(submit_result.get("message"))

        st.markdown("**真实订单记录 / 状态回查 / 手动撤单**")
        for row in load_live_order_records(20):
            order_id = str(row.get("order_id", ""))
            symbol = str(row.get("symbol", ""))
            with st.expander(f"{row.get('time')}｜{symbol}｜{row.get('side')}｜{row.get('order_status')}｜{order_id}", expanded=False):
                st.markdown(kline_symbol_link(symbol, f"查看 {symbol} K线", "watch-pill"), unsafe_allow_html=True)
                st.markdown(f"金额：{row.get('notional')}｜数量：{row.get('quantity')}｜来源：{row.get('source')}｜人工干预：{row.get('manual_override')}")
                rc1, rc2 = st.columns(2)
                if rc1.button("回查订单状态", key=f"fetch_live_order_{order_id}", use_container_width=True, disabled=not order_id):
                    st.session_state[f"live_order_status_{order_id}"] = fetch_live_order_status(order_id, symbol)
                cancel_confirm = rc2.checkbox("我确认撤销该真实订单", key=f"cancel_confirm_{order_id}", disabled=not order_id)
                if rc2.button("撤销真实订单", key=f"cancel_live_order_{order_id}", use_container_width=True, disabled=not order_id or not cancel_confirm):
                    st.session_state[f"live_order_status_{order_id}"] = cancel_live_order(order_id, symbol, cancel_confirm)
                status_result = st.session_state.get(f"live_order_status_{order_id}")
                if status_result:
                    (st.success if status_result.get("ok") else st.warning)(status_result.get("message"))

    with tabs[7]:
        st.markdown(
            """
            <div class="app-shell"><div class="module-card warning-box">
              <b>小资金实盘持仓管理提示</b><br>
              当前为真实 Spot 持仓只读识别与人工确认平仓功能。系统不会自动卖出真实资产，任何平仓或减仓都必须由你手动确认并输入确认短句。
            </div></div>
            """,
            unsafe_allow_html=True,
        )
        records = load_live_order_records(300)
        symbols_for_price = sorted({str(row.get("symbol", "")).upper() for row in records if row.get("symbol")})
        current_prices: dict[str, float] = {}
        for sym in symbols_for_price:
            ticker_row = market_cache.get_ticker(sym) or {}
            current_prices[sym] = float(ticker_row.get("last_price") or 0)
        live_pos_summary = get_live_position_summary(current_prices)
        render_metric_grid(
            [
                ("系统实盘持仓", str(live_pos_summary.get("system_position_count", 0)), "green" if live_pos_summary.get("system_position_count", 0) else "yellow"),
                ("外部资产", str(live_pos_summary.get("external_asset_count", 0)), "yellow"),
                ("浮动盈亏估算", f"{float(live_pos_summary.get('total_unrealized_pnl', 0) or 0):+.4f} USDT", "green" if float(live_pos_summary.get("total_unrealized_pnl", 0) or 0) >= 0 else "red"),
                ("安全锁", "已开启" if settings.get("kill_switch_enabled") else "未开启", "red" if settings.get("kill_switch_enabled") else "green"),
                ("Live Manual", "已开启" if settings.get("live_manual_enabled") else "未开启", "green" if settings.get("live_manual_enabled") else "red"),
                ("真实提交", "允许检查" if exec_status.get("live_trading_enabled") else "默认关闭", "green" if exec_status.get("live_trading_enabled") else "red"),
            ]
        )

        system_positions = live_pos_summary.get("open_system_positions") or []
        if not system_positions:
            st.info("当前暂无系统实盘持仓。外部资产会在下方只读显示，不纳入系统策略统计。")
        for pos in system_positions:
            pos_id = str(pos.get("live_position_id", ""))
            symbol = str(pos.get("symbol", ""))
            review = generate_live_position_review_snapshot(pos)
            pnl = float(pos.get("unrealized_pnl", 0) or 0)
            pnl_pct = float(pos.get("unrealized_pnl_pct", 0) or 0)
            with st.expander(f"{symbol} 实盘现货持仓｜{pos.get('status')}｜浮盈 {pnl:+.4f} USDT / {pnl_pct:+.2f}%", expanded=True):
                st.markdown(kline_symbol_link(symbol, f"查看 {symbol} K线", "watch-pill"), unsafe_allow_html=True)
                render_metric_grid(
                    [
                        ("来源", "系统订单", "green"),
                        ("买入均价", f"{float(pos.get('avg_entry_price', 0) or 0):.8f}", ""),
                        ("当前价格", f"{float(pos.get('current_price', 0) or 0):.8f}", ""),
                        ("原始数量", f"{float(pos.get('original_quantity', 0) or 0):.8f}", ""),
                        ("剩余数量", f"{float(pos.get('remaining_quantity', 0) or 0):.8f}", "yellow"),
                        ("成本", f"{float(pos.get('quote_cost', 0) or 0):.4f} {pos.get('quote_asset', 'USDT')}", ""),
                        ("已实现盈亏", f"{float(pos.get('realized_pnl', 0) or 0):+.4f}", "green" if float(pos.get("realized_pnl", 0) or 0) >= 0 else "red"),
                        ("风险", str(pos.get("risk_level", "低")), "red" if pos.get("risk_level") in {"高", "极高"} else "yellow"),
                    ]
                )
                st.markdown(
                    f"""
                    <div class="status-card">
                      <b>持仓复核</b><br>
                      AI建议：{escape(str(review.get("hold_decision", "继续观察")))}｜
                      风险等级：{escape(str(review.get("risk_level", "低")))}｜
                      自动动作：{escape(str(review.get("auto_action", "none")))}<br>
                      系统仅提醒，不会自动卖出真实资产。
                    </div>
                    """,
                    unsafe_allow_html=True,
                )
                c1, c2, c3, c4 = st.columns(4)
                ratio_label = c1.selectbox("平仓比例", ["25%", "50%", "75%", "100%"], index=1, key=f"exit_ratio_{pos_id}")
                order_type = c2.selectbox("平仓类型", ["LIMIT", "MARKET"], key=f"exit_type_{pos_id}", format_func=lambda x: "限价 LIMIT" if x == "LIMIT" else "市价 MARKET（注意滑点）")
                default_exit_price = float(pos.get("current_price", 0) or pos.get("avg_entry_price", 0) or 0)
                exit_price = c3.number_input("平仓价格", min_value=0.0, value=default_exit_price, step=0.01, key=f"exit_price_{pos_id}")
                exit_reason = c4.selectbox("平仓原因", ["用户手动", "建议止盈", "风险升高", "信号失效"], key=f"exit_reason_{pos_id}")
                if st.button("生成平仓预览", key=f"create_exit_plan_{pos_id}", use_container_width=True):
                    ratio = float(ratio_label.strip("%")) / 100
                    st.session_state[f"live_exit_plan_{pos_id}"] = create_live_exit_plan(pos, {"exit_ratio": ratio, "order_type": order_type, "price": exit_price, "exit_reason": exit_reason})
                    st.session_state.pop(f"live_exit_test_{pos_id}", None)
                    st.session_state.pop(f"live_exit_preflight_{pos_id}", None)
                    st.rerun()

                exit_plan = st.session_state.get(f"live_exit_plan_{pos_id}")
                if exit_plan:
                    exit_preview = preview_live_exit_order(exit_plan)
                    ep = exit_preview.get("exit_plan", {})
                    st.markdown(
                        f"""
                        <div class="status-card">
                          <b>平仓预览：尚未执行真实卖出</b><br>
                          本次平仓：{float(ep.get("exit_ratio", 0) or 0) * 100:.0f}%｜
                          数量：{float(ep.get("exit_quantity", 0) or 0):.8f}｜
                          预计成交额：{float(ep.get("estimated_value", 0) or 0):.4f} USDT｜
                          预计手续费：{float(ep.get("estimated_fee", 0) or 0):.4f}｜
                          预计盈亏：{float(ep.get("estimated_pnl", 0) or 0):+.4f} USDT<br>
                          平仓后剩余数量：{float(exit_preview.get("remaining_after_exit", 0) or 0):.8f}
                        </div>
                        """,
                        unsafe_allow_html=True,
                    )
                    validation = exit_preview.get("validation") or {}
                    for err in validation.get("errors", []):
                        st.error(err)
                    for warn in validation.get("warnings", []):
                        st.warning(warn)
                    if ep.get("order_type") == "MARKET":
                        st.warning("市价平仓可能产生滑点，实际成交价格可能与当前价格不同。")
                    if float(ep.get("exit_ratio", 0) or 0) >= 0.999:
                        st.warning("该操作将尝试卖出该系统持仓的全部剩余数量。")
                    if st.button("执行平仓 Spot Test Order 验证", key=f"exit_test_{pos_id}", use_container_width=True):
                        st.session_state[f"live_exit_test_{pos_id}"] = run_exit_spot_test_order(exit_plan)
                        st.rerun()
                    test_result = st.session_state.get(f"live_exit_test_{pos_id}") or {}
                    if test_result:
                        (st.success if test_result.get("ok") else st.error)(test_result.get("message"))
                    st.markdown("**平仓人工确认区**")
                    confirmed = st.checkbox("我确认这是小资金真实平仓订单，并理解风险", key=f"exit_confirm_{pos_id}")
                    phrase = st.text_input("请输入确认短句：我确认执行小资金实盘平仓", key=f"exit_phrase_{pos_id}")
                    if st.button("运行平仓执行前检查", key=f"exit_preflight_{pos_id}", use_container_width=True):
                        st.session_state[f"live_exit_preflight_{pos_id}"] = run_live_exit_preflight(exit_plan, test_result, confirmed, phrase)
                    exit_preflight = st.session_state.get(f"live_exit_preflight_{pos_id}") or {}
                    if exit_preflight:
                        st.markdown(f"**检查结果：{exit_preflight.get('message')}**")
                        for item in exit_preflight.get("checklist", []):
                            if item.get("status") == "通过":
                                st.success(f"{item.get('name')}：{item.get('message')}")
                            else:
                                st.error(f"{item.get('name')}：{item.get('message')}")
                    if st.button("提交真实 Spot 平仓订单", key=f"submit_exit_{pos_id}", disabled=not bool(exit_preflight.get("ok")), use_container_width=True):
                        st.session_state[f"live_exit_submit_{pos_id}"] = submit_live_exit_order(exit_plan, test_result, confirmed, phrase)
                        st.rerun()
                    submit_exit = st.session_state.get(f"live_exit_submit_{pos_id}") or {}
                    if submit_exit:
                        (st.success if submit_exit.get("ok") else st.error)(submit_exit.get("message"))

        st.markdown("**外部持仓 / 不明资产（只读）**")
        external_assets = live_pos_summary.get("external_assets") or []
        if not external_assets:
            st.info("当前暂无外部持仓或不明来源资产。")
        for asset in external_assets[:30]:
            st.markdown(
                f"""
                <div class="status-card">
                  <b>{escape(str(asset.get("asset", "-")))}</b>｜来源：{escape(str(asset.get("source", "external")))}<br>
                  可用：{float(asset.get("free", 0) or 0):.8f}｜锁定：{float(asset.get("locked", 0) or 0):.8f}｜总量：{float(asset.get("total", 0) or 0):.8f}<br>
                  {escape(str(asset.get("message", "外部持仓只读显示。")))}
                </div>
                """,
                unsafe_allow_html=True,
            )

        st.markdown("**实盘持仓审计日志**")
        position_audit = load_live_position_audit_log(30)
        if not position_audit:
            st.info("暂无实盘持仓审计日志。")
        for event in position_audit:
            st.caption(f"{event.get('time')}｜{event.get('event')}｜{event.get('symbol')}｜{event.get('result')}｜{event.get('reason')}")

    with tabs[8]:
        st.markdown(
            """
            <div class="app-shell"><div class="module-card warning-box">
              <b>小资金自动实盘试运行提示</b><br>
              LIVE_AUTO_PILOT 是小资金全自动试运行层。默认关闭，只有通过准入检查、白名单、额度限制、冷却、熔断、交易所规则和对应 Test Order 后，才允许提交极小资金真实 Spot 或 U本位合约订单。
            </div></div>
            """,
            unsafe_allow_html=True,
        )
        auto_config = load_live_auto_config()
        auto_symbols = set(auto_config.get("allowed_symbols") or [])
        auto_positions_snapshot = get_live_auto_status().get("open_positions") or []
        for pos in auto_positions_snapshot:
            if pos.get("symbol"):
                auto_symbols.add(str(pos.get("symbol")).upper())
        auto_prices: dict[str, float] = {}
        for sym in sorted(auto_symbols):
            ticker_row = market_cache.get_ticker(sym) or {}
            auto_prices[sym] = float(ticker_row.get("last_price") or 0)
        auto_status = get_live_auto_status(auto_prices)
        auto_config = auto_status.get("config") or auto_config
        auto_review = auto_status.get("review") or {}
        render_metric_grid(
            [
                ("自动模式", "开启" if auto_status.get("enabled") else "关闭", "green" if auto_status.get("enabled") else "red"),
                ("自动下单", "开启" if auto_status.get("order_enabled") else "关闭", "green" if auto_status.get("order_enabled") else "yellow"),
                ("自动止盈止损", "开启" if auto_status.get("exit_enabled") else "关闭", "green" if auto_status.get("exit_enabled") else "yellow"),
                ("暂停状态", "已暂停" if auto_status.get("paused") else "运行/待命", "yellow" if auto_status.get("paused") else "green"),
                ("熔断", "已触发" if auto_status.get("circuit_breaker_enabled") else "未触发", "red" if auto_status.get("circuit_breaker_enabled") else "green"),
                ("今日额度", f"{float(auto_status.get('daily_used_usdt', 0) or 0):.2f} / {float(auto_config.get('daily_limit_usdt', 20) or 20):.2f} USDT", "yellow"),
                ("单笔上限", f"{float(auto_config.get('max_order_usdt', 5) or 5):.2f} USDT", "yellow"),
                ("自动持仓", str(len(auto_status.get("open_positions") or [])), "yellow"),
            ]
        )
        if auto_status.get("circuit_breaker_reason"):
            st.error(f"自动实盘熔断原因：{auto_status.get('circuit_breaker_reason')}")

        cfg_col, ctl_col = st.columns(2)
        with cfg_col:
            st.markdown("**自动试运行配置**")
            with st.form("live_auto_config_form"):
                new_config = dict(auto_config)
                symbols_text = st.text_input("自动实盘白名单", value=",".join(auto_config.get("allowed_symbols") or ["BTCUSDT", "ETHUSDT"]))
                new_config["allowed_symbols"] = [s.strip().upper() for s in symbols_text.split(",") if s.strip()]
                new_config["max_order_usdt"] = st.number_input("单笔自动真实订单上限 USDT", min_value=1.0, max_value=50.0, value=float(auto_config.get("max_order_usdt", 5) or 5), step=1.0)
                new_config["daily_limit_usdt"] = st.number_input("单日自动真实额度上限 USDT", min_value=5.0, max_value=200.0, value=float(auto_config.get("daily_limit_usdt", 20) or 20), step=5.0)
                new_config["max_positions"] = st.number_input("最大同时自动真实持仓", min_value=1, max_value=5, value=int(auto_config.get("max_positions", 1) or 1), step=1)
                new_config["allow_futures"] = st.checkbox("允许 U本位永续合约自动实盘", value=bool(auto_config.get("allow_futures", True)))
                market_options = ["spot", "futures"]
                market_index = market_options.index(str(auto_config.get("default_market_type", "futures"))) if str(auto_config.get("default_market_type", "futures")) in market_options else 1
                new_config["default_market_type"] = st.selectbox("默认自动实盘市场", market_options, index=market_index, format_func=lambda x: "现货 Spot" if x == "spot" else "U本位永续合约")
                new_config["default_leverage"] = st.slider("实盘合约默认杠杆", 1, 20, int(auto_config.get("default_leverage", 5) or 5), help="系统会在下合约订单前同步 Binance 当前交易对杠杆。")
                new_config["max_leverage"] = st.slider("实盘合约最大允许杠杆", 1, 125, int(auto_config.get("max_leverage", 20) or 20))
                new_config["global_cooldown_minutes"] = st.number_input("全局冷却分钟", min_value=1, max_value=240, value=int(auto_config.get("global_cooldown_minutes", 15) or 15), step=1)
                new_config["symbol_cooldown_minutes"] = st.number_input("同币种冷却分钟", min_value=5, max_value=720, value=int(auto_config.get("symbol_cooldown_minutes", 60) or 60), step=5)
                new_config["allow_market_order"] = st.checkbox("允许自动市价单（默认不建议）", value=bool(auto_config.get("allow_market_order")))
                new_config["live_auto_order_enabled"] = st.checkbox("允许自动小资金真实下单", value=bool(auto_config.get("live_auto_order_enabled")))
                exit_phrase = st.text_input("开启自动止盈止损确认短句", placeholder="我确认开启小资金自动止盈止损")
                requested_exit = st.checkbox("允许自动止盈/止损试运行", value=bool(auto_config.get("live_auto_exit_enabled")))
                new_config["live_auto_exit_enabled"] = bool(requested_exit and exit_phrase.strip() == "我确认开启小资金自动止盈止损")
                new_config["take_profit_pct"] = st.number_input("止盈阈值 %", min_value=0.1, max_value=20.0, value=float(auto_config.get("take_profit_pct", 1.5) or 1.5), step=0.1)
                new_config["stop_loss_pct"] = st.number_input("止损阈值 %", min_value=-20.0, max_value=-0.1, value=float(auto_config.get("stop_loss_pct", -1.0) or -1.0), step=0.1)
                if st.form_submit_button("保存自动试运行配置", use_container_width=True):
                    save_live_auto_config(new_config)
                    st.success("自动试运行配置已保存。自动实盘仍默认受准入检查和总开关限制。")
                    st.rerun()
                if requested_exit and exit_phrase.strip() != "我确认开启小资金自动止盈止损":
                    st.caption("自动止盈止损未开启：确认短句不匹配。")

        with ctl_col:
            st.markdown("**手机端控制台 / 总开关**")
            confirm_phrase = st.text_input("开启/恢复确认短句", placeholder="我确认开启小资金自动实盘试运行", key="live_auto_confirm_phrase")
            c1, c2 = st.columns(2)
            if c1.button("开启自动试运行", use_container_width=True):
                result = enable_live_auto_pilot(confirm_phrase)
                (st.success if result.get("ok") else st.error)(result.get("message"))
                if not result.get("ok") and result.get("admission"):
                    st.session_state["live_auto_last_admission"] = result.get("admission")
                st.rerun()
            if c2.button("恢复自动试运行", use_container_width=True):
                result = resume_live_auto_pilot(confirm_phrase)
                (st.success if result.get("ok") else st.error)(result.get("message"))
                st.rerun()
            p1, p2 = st.columns(2)
            if p1.button("暂停自动试运行", use_container_width=True):
                pause_live_auto_pilot("用户在自动试运行控制台暂停。")
                st.rerun()
            if p2.button("关闭自动试运行", use_container_width=True):
                disable_live_auto_pilot("用户在自动试运行控制台关闭。")
                st.rerun()
            breaker_reason = st.text_input("熔断/紧急停止原因", value="用户手动触发自动实盘熔断。")
            b1, b2 = st.columns(2)
            if b1.button("触发自动熔断", use_container_width=True):
                trigger_live_auto_circuit_breaker(breaker_reason)
                st.rerun()
            release_phrase = b2.text_input("解除熔断短句", placeholder="我确认解除自动实盘熔断")
            if b2.button("解除自动熔断", use_container_width=True):
                result = release_live_auto_circuit_breaker(release_phrase)
                (st.success if result.get("ok") else st.error)(result.get("message"))
                st.rerun()

        st.markdown("**自动实盘准入检查**")
        if st.button("运行准入检查", use_container_width=True):
            st.session_state["live_auto_last_admission"] = run_live_auto_admission_check(user_confirmed=False)
        admission = st.session_state.get("live_auto_last_admission") or auto_status.get("admission") or {}
        for item in admission.get("checks", []):
            if item.get("ok"):
                st.success(f"{item.get('name')}：{item.get('message')}")
            else:
                st.error(f"{item.get('name')}：{item.get('message')}")

        st.markdown("**当前交易对象自动实盘信号检查**")
        current_symbol = st.session_state.get("current_symbol", "BTCUSDT")
        current_ticker = market_cache.get_ticker(current_symbol) or {}
        decision = build_current_committee_decision(current_symbol, current_ticker)
        live_auto_signal = committee_decision_to_sim_signal(decision)
        live_auto_signal["symbol"] = current_symbol
        live_auto_signal["current_price"] = float(current_ticker.get("last_price") or 0)
        live_auto_signal["data_quality"] = "good" if current_ticker else "poor"
        is_signal_ok, signal_reasons = filter_live_auto_signal(live_auto_signal)
        render_metric_grid(
            [
                ("交易对象", current_symbol, "yellow"),
                ("最终动作", str(live_auto_signal.get("action", "-")), ""),
                ("委员会置信度", str(live_auto_signal.get("committee_confidence", 0)), "green" if float(live_auto_signal.get("committee_confidence") or 0) >= 70 else "yellow"),
                ("风险评分", format_score(live_auto_signal.get("risk_score")), "green" if safe_compare_lt(live_auto_signal.get("risk_score"), 55.01) else "red"),
                ("数据质量", str(live_auto_signal.get("data_quality", "poor")), "green" if live_auto_signal.get("data_quality") == "good" else "red"),
                ("过滤结果", "可生成自动计划" if is_signal_ok else "拒绝自动实盘", "green" if is_signal_ok else "red"),
            ]
        )
        if signal_reasons:
            for reason in signal_reasons:
                st.warning(reason)
            st.caption("不满足自动交易条件的信号不会丢弃，会保留在观察/自动候选中等待下一轮复核。")
        sig_c1, sig_c2 = st.columns(2)
        if sig_c1.button("生成自动订单计划", disabled=not is_signal_ok, use_container_width=True):
            st.session_state["live_auto_order_plan"] = create_live_auto_order_plan(live_auto_signal)
            st.rerun()
        if sig_c2.button("重新刷新自动信号", use_container_width=True):
            st.session_state["live_auto_signal"] = _build_live_auto_signal()
            st.success("已刷新自动交易候选信号。")

        auto_plan = st.session_state.get("live_auto_order_plan")
        if auto_plan:
            st.markdown("**自动订单计划 / 执行前检查**")
            render_metric_grid(
                [
                    ("计划ID", str(auto_plan.get("auto_plan_id", "-")), ""),
                    ("交易对象", str(auto_plan.get("symbol", "-")), "yellow"),
                    ("方向", str(auto_plan.get("side", "-")), "green"),
                    ("类型", str(auto_plan.get("order_type", "-")), ""),
                    ("价格", f"{float(auto_plan.get('price', 0) or 0):.8f}", ""),
                    ("金额", f"{float(auto_plan.get('quote_amount', 0) or 0):.2f} USDT", "yellow"),
                ]
            )
            st.caption("该计划不是订单。点击执行前，仍需通过自动试运行开关、白名单、额度、交易所规则和对应 Test Order。")
            if st.button("执行自动试运行订单（极小资金）", use_container_width=True):
                st.session_state["live_auto_execute_result"] = execute_live_auto_spot_order(auto_plan)
                st.rerun()
            auto_exec = st.session_state.get("live_auto_execute_result") or {}
            if auto_exec:
                (st.success if auto_exec.get("ok") else st.error)(auto_exec.get("message"))
                if auto_exec.get("converted_to_approval"):
                    st.warning("自动执行未通过，系统已保留信号等待下一轮复核。")
                preflight = auto_exec.get("preflight") or {}
                for item in preflight.get("checks", []):
                    st.caption(f"{'通过' if item.get('ok') else '失败'}｜{item.get('name')}｜{item.get('message')}")

        st.markdown("**自动实盘持仓监控**")
        open_auto_positions = auto_status.get("open_positions") or []
        if not open_auto_positions:
            st.info("当前暂无自动实盘持仓。")
        for pos in open_auto_positions:
            exit_check = run_live_auto_exit_check(pos)
            with st.expander(f"{pos.get('symbol')}｜{pos.get('status')}｜浮盈 {float(pos.get('unrealized_pnl', 0) or 0):+.4f} USDT / {float(pos.get('unrealized_pnl_pct', 0) or 0):+.2f}%", expanded=False):
                render_metric_grid(
                    [
                        ("入场价", f"{float(pos.get('entry_price', 0) or 0):.8f}", ""),
                        ("当前价", f"{float(pos.get('current_price', 0) or 0):.8f}", ""),
                        ("数量", f"{float(pos.get('quantity', 0) or 0):.8f}", ""),
                        ("金额", f"{float(pos.get('quote_amount', 0) or 0):.2f} USDT", "yellow"),
                        ("退出检查", str(exit_check.get("action", "-")), "red" if exit_check.get("ok") else "yellow"),
                        ("处理方式", str(exit_check.get("message", "")), ""),
                    ]
                )
                st.caption("自动退出默认关闭。即使开启，当前版本仍以极小仓位和审计优先；异常时只提醒不卖出。")

        st.markdown("**自动实盘复盘统计**")
        render_metric_grid(
            [
                ("自动订单数", str(auto_review.get("auto_order_count", 0)), ""),
                ("成功数", str(auto_review.get("auto_success_count", 0)), "green"),
                ("失败数", str(auto_review.get("auto_failure_count", 0)), "red"),
                ("熔断次数", str(auto_review.get("circuit_breaker_count", 0)), "red"),
            ]
        )
        st.info(auto_review.get("sample_warning", "自动实盘样本不足，暂不建议扩大额度或进入正式自动交易。"))

        st.markdown("**自动实盘审计日志**")
        auto_audit = load_live_auto_audit_log(50)
        if not auto_audit:
            st.info("暂无自动实盘审计日志。")
        for event in auto_audit:
            st.caption(f"{event.get('time')}｜{event.get('event')}｜{event.get('symbol')}｜{event.get('result')}｜{event.get('reason')}")

    with tabs[9]:
        sim_stats = status.get("sim_stats") or {}
        replay_summary = status.get("replay_summary") or {}
        st.markdown(f"模拟交易次数：{sim_stats.get('total_trades', 0)}｜模拟胜率：{float(sim_stats.get('win_rate', 0) or 0):.2f}%｜Profit Factor：{float(sim_stats.get('profit_factor', 0) or 0):.2f}｜最大回撤：{float(sim_stats.get('max_drawdown', 0) or 0):.2f}%")
        st.markdown(f"复盘数据质量：{replay_summary.get('data_quality', 'poor')}｜{replay_summary.get('sample_warning', '')}")
        allowed = status.get("allowed_strategy_candidates") or []
        all_candidates = status.get("strategy_candidates") or []
        if not all_candidates:
            st.info("暂无策略工厂候选策略。")
        for candidate in all_candidates[:30]:
            is_allowed = candidate in allowed
            st.markdown(
                f"""
                <div class="status-card">
                  <b>{escape(str(candidate.get("strategy_name")))}</b>｜评级：{escape(str(candidate.get("grade")))}｜过拟合：{escape(str(candidate.get("overfit_risk")))}<br>
                  实盘准入：{'允许进入后续审查' if is_allowed else '拒绝'}<br>
                  原因：{'候选策略评级和过拟合风险满足模拟验证入口。' if is_allowed else '该策略尚未完成足够模拟验证，或评级/过拟合风险不达标。'}
                </div>
                """,
                unsafe_allow_html=True,
            )

    with tabs[10]:
        audit = status.get("recent_audit") or []
        if not audit:
            st.info("暂无实盘安全审计日志。")
        for event in audit[:100]:
            st.caption(f"{event.get('time')}｜{event.get('event')}｜{event.get('mode')}｜{event.get('symbol')}｜{event.get('result')}｜{event.get('reason')}")


def render_approval_center_page() -> None:
    """自动交易控制台。保留函数名仅用于兼容旧 approval 路由。"""
    render_page_head("auto_trade")
    secure_status = get_secure_api_status()
    binance_ready = bool((secure_status.get("BINANCE_API_KEY") or {}).get("configured")) and bool((secure_status.get("BINANCE_API_SECRET") or {}).get("configured"))
    auto_config = load_live_auto_config()
    auto_symbols = set(auto_config.get("allowed_symbols") or [])
    for pos in (get_live_auto_status().get("open_positions") or []):
        if pos.get("symbol"):
            auto_symbols.add(str(pos.get("symbol")).upper())
    auto_prices = {sym: float((market_cache.get_ticker(sym) or {}).get("last_price") or 0) for sym in sorted(auto_symbols)}
    auto_status = get_live_auto_status(auto_prices)
    auto_config = auto_status.get("config") or auto_config
    auto_review = auto_status.get("review") or {}

    st.markdown(
        """
        <div class="app-shell">
          <div class="module-card warning-box">
            <b>自动交易栏</b><br>
            审批制度已取消。本栏用于自动实盘交易控制，支持现货 Spot 与 U本位永续合约。开启后系统会按用户设置的本金、开仓比例、杠杆、白名单、止盈止损和风控状态执行；未接入有效 Binance API 时只显示待接入，不会下单。
          </div>
        </div>
        """,
        unsafe_allow_html=True,
    )
    render_metric_grid(
        [
            ("Binance API", "已接入" if binance_ready else "待接入", "green" if binance_ready else "yellow"),
            ("自动交易状态", "运行中" if auto_status.get("enabled") and not auto_status.get("paused") else "暂停" if auto_status.get("paused") else "关闭", "green" if auto_status.get("enabled") and not auto_status.get("paused") else "yellow"),
            ("自动下单", "开启" if auto_status.get("order_enabled") else "关闭", "green" if auto_status.get("order_enabled") else "yellow"),
            ("自动止盈止损", "开启" if auto_status.get("exit_enabled") else "关闭", "green" if auto_status.get("exit_enabled") else "yellow"),
            ("市场", ("现货 " if auto_config.get("allow_spot") else "") + ("永续" if auto_config.get("allow_futures") else ""), "blue"),
            ("默认杠杆", f"{int(auto_config.get('default_leverage', 5) or 5)}x", "yellow"),
            ("开仓比例", f"{float(auto_config.get('position_pct', 5) or 5):.2f}%", "yellow"),
            ("单笔保证金", f"{float(auto_config.get('max_order_usdt', 0) or 0):.2f} USDT", "yellow"),
        ]
    )

    if not binance_ready:
        st.warning("Binance API 待接入：自动交易不会运行。请在下方填写并保存 Binance API Key / Secret。")
        with st.form("auto_trade_binance_api_form"):
            b1, b2 = st.columns(2)
            binance_key = b1.text_input("Binance API Key", value="", type="password", placeholder="留空则不修改")
            binance_secret = b2.text_input("Binance API Secret", value="", type="password", placeholder="留空则不修改")
            st.caption("密钥保存机制与 DeepSeek/Gemini 一致：写入本机 .env，页面只显示脱敏状态，不记录 Secret。")
            if st.form_submit_button("保存 Binance API", use_container_width=True):
                result = write_secure_api_values({"BINANCE_API_KEY": binance_key, "BINANCE_API_SECRET": binance_secret})
                (st.success if result.get("ok") else st.warning)(result.get("message"))
                st.rerun()
        return

    account_snapshot = get_live_account_snapshot(False, "futures" if auto_config.get("allow_futures") else "spot")
    tabs = st.tabs(["账户总览", "自动开关", "当前信号", "订单计划", "自动持仓", "参数设置", "事件日志"])

    with tabs[0]:
        if account_snapshot.get("ok"):
            st.success("Binance API 已接入，账户只读检查通过。")
        else:
            st.warning(account_snapshot.get("message", "Binance账户状态暂不可用。"))
        render_metric_grid(
            [
                ("今日额度", f"{float(auto_status.get('daily_used_usdt', 0) or 0):.2f} / {float(auto_config.get('daily_limit_usdt', 0) or 0):.2f} USDT", "yellow"),
                ("本金设置", f"{float(auto_config.get('principal_usdt', 0) or 0):.2f} USDT", "blue"),
                ("最大持仓", str(auto_config.get("max_positions", 1)), ""),
                ("当前持仓", str(len(auto_status.get("open_positions") or [])), "yellow"),
                ("熔断", "已触发" if auto_status.get("circuit_breaker_enabled") else "未触发", "red" if auto_status.get("circuit_breaker_enabled") else "green"),
                ("熔断原因", str(auto_status.get("circuit_breaker_reason") or "无")[:32], "red" if auto_status.get("circuit_breaker_enabled") else ""),
            ]
        )
        balances = account_snapshot.get("balances") or []
        if balances:
            st.caption("账户余额只读摘要")
            st.dataframe(balances[:20], use_container_width=True, hide_index=True)

    with tabs[1]:
        c1, c2, c3, c4 = st.columns(4)
        if c1.button("开启自动交易", use_container_width=True):
            result = enable_live_auto_pilot("我确认开启小资金自动实盘试运行")
            (st.success if result.get("ok") else st.error)(result.get("message"))
            st.rerun()
        if c2.button("暂停自动交易", use_container_width=True):
            pause_live_auto_pilot("用户在自动交易栏暂停。")
            st.rerun()
        if c3.button("恢复自动交易", use_container_width=True):
            result = resume_live_auto_pilot("我确认开启小资金自动实盘试运行")
            (st.success if result.get("ok") else st.error)(result.get("message"))
            st.rerun()
        if c4.button("停止自动交易", use_container_width=True):
            disable_live_auto_pilot("用户在自动交易栏停止。")
            st.rerun()
        b1, b2 = st.columns(2)
        breaker_reason = b1.text_input("熔断原因", value="用户手动触发自动交易熔断。")
        if b1.button("触发熔断", use_container_width=True):
            trigger_live_auto_circuit_breaker(breaker_reason)
            st.rerun()
        if b2.button("解除熔断", use_container_width=True):
            result = release_live_auto_circuit_breaker("我确认解除自动实盘熔断")
            (st.success if result.get("ok") else st.error)(result.get("message"))
            st.rerun()
        st.markdown("**自动交易准入检查**")
        admission = run_live_auto_admission_check(user_confirmed=True)
        for item in admission.get("checks", []):
            (st.success if item.get("ok") else st.error)(f"{item.get('name')}：{item.get('message')}")

    with tabs[2]:
        current_symbol = st.session_state.get("current_symbol", "BTCUSDT")
        current_ticker = market_cache.get_ticker(current_symbol) or {}
        decision = build_current_committee_decision(current_symbol, current_ticker)
        signal = committee_decision_to_sim_signal(decision)
        signal["symbol"] = current_symbol
        signal["current_price"] = float(current_ticker.get("last_price") or 0)
        signal["data_quality"] = "good" if current_ticker else "poor"
        ok, reasons = filter_live_auto_signal(signal)
        render_metric_grid(
            [
                ("交易对象", current_symbol, "yellow"),
                ("当前价格", format_price(signal.get("current_price")), ""),
                ("委员会动作", str(signal.get("action", "-")), ""),
                ("置信度", str(signal.get("committee_confidence", 0)), "green" if float(signal.get("committee_confidence") or 0) >= 70 else "yellow"),
                ("风险评分", format_score(signal.get("risk_score")), "green" if safe_compare_lt(signal.get("risk_score"), 55.01) else "red"),
                ("信号过滤", "通过" if ok else "拒绝", "green" if ok else "red"),
            ]
        )
        for reason in reasons:
            st.warning(reason)
        if st.button("按当前信号生成自动订单计划", disabled=not ok, use_container_width=True):
            st.session_state["live_auto_order_plan"] = create_live_auto_order_plan(signal)
            st.rerun()

    with tabs[3]:
        plan = st.session_state.get("live_auto_order_plan")
        if not plan:
            st.info("当前暂无自动订单计划。请在“当前信号”页生成。")
        else:
            render_metric_grid(
                [
                    ("计划ID", str(plan.get("auto_plan_id", "-")), ""),
                    ("交易对象", str(plan.get("symbol", "-")), "yellow"),
                    ("市场", "永续合约" if plan.get("market_type") == "futures" else "现货", "blue"),
                    ("方向", str(plan.get("side", "-")), "green" if plan.get("side") == "BUY" else "red"),
                    ("杠杆", f"{int(plan.get('leverage', 1) or 1)}x", "yellow"),
                    ("保证金", f"{float(plan.get('quote_amount', 0) or 0):.2f} USDT", "yellow"),
                    ("名义金额", f"{float(plan.get('notional', 0) or 0):.2f} USDT", "blue"),
                    ("数量", f"{float(plan.get('quantity', 0) or 0):.8f}", ""),
                ]
            )
            if st.button("执行自动订单", use_container_width=True):
                st.session_state["live_auto_execute_result"] = execute_live_auto_spot_order(plan)
                st.rerun()
            result = st.session_state.get("live_auto_execute_result") or {}
            if result:
                (st.success if result.get("ok") else st.error)(result.get("message"))
                preflight = result.get("preflight") or {}
                for item in preflight.get("checks", []):
                    st.caption(f"{'通过' if item.get('ok') else '失败'}｜{item.get('name')}｜{item.get('message')}")

    with tabs[4]:
        positions = auto_status.get("open_positions") or []
        if not positions:
            st.info("当前暂无自动交易持仓。")
        for pos in positions:
            with st.expander(f"{pos.get('symbol')}｜{pos.get('market_type', 'spot')}｜{pos.get('status')}｜浮盈 {float(pos.get('unrealized_pnl', 0) or 0):+.4f} USDT / {float(pos.get('unrealized_pnl_pct', 0) or 0):+.2f}%", expanded=False):
                st.markdown(kline_symbol_link(pos.get("symbol"), f"查看 {pos.get('symbol')} K线", "watch-pill"), unsafe_allow_html=True)
                exit_check = run_live_auto_exit_check(pos)
                render_metric_grid(
                    [
                        ("入场价", f"{float(pos.get('entry_price', 0) or 0):.8f}", ""),
                        ("当前价", f"{float(pos.get('current_price', 0) or 0):.8f}", ""),
                        ("数量", f"{float(pos.get('quantity', 0) or 0):.8f}", ""),
                        ("保证金", f"{float(pos.get('quote_amount', 0) or 0):.2f} USDT", "yellow"),
                        ("杠杆", f"{int(pos.get('leverage', 1) or 1)}x", "yellow"),
                        ("退出检查", str(exit_check.get("action", "-")), "red" if exit_check.get("ok") else "yellow"),
                    ]
                )

    with tabs[5]:
        with st.form("auto_trade_settings_form"):
            new_config = dict(auto_config)
            new_config["principal_usdt"] = st.number_input("自动交易本金 USDT", min_value=1.0, max_value=10_000_000.0, value=float(auto_config.get("principal_usdt", 100) or 100), step=10.0)
            new_config["position_pct"] = st.slider("开仓比例（占本金）", min_value=0.1, max_value=40.0, value=float(auto_config.get("position_pct", 5) or 5), step=0.1)
            st.info(f"按当前设置，单笔保证金约为 {float(new_config['principal_usdt']) * float(new_config['position_pct']) / 100:.2f} USDT。")
            new_config["daily_limit_usdt"] = st.number_input("单日自动交易额度 USDT", min_value=1.0, max_value=float(new_config["principal_usdt"]), value=min(float(auto_config.get("daily_limit_usdt", 20) or 20), float(new_config["principal_usdt"])), step=10.0)
            new_config["max_positions"] = st.number_input("最大同时持仓", min_value=1, max_value=5, value=int(auto_config.get("max_positions", 1) or 1), step=1)
            new_config["allowed_symbols"] = [s.strip().upper() for s in st.text_input("自动交易白名单", value=",".join(auto_config.get("allowed_symbols") or ["BTCUSDT", "ETHUSDT"])).split(",") if s.strip()]
            c1, c2 = st.columns(2)
            new_config["allow_spot"] = c1.checkbox("允许现货自动交易", value=bool(auto_config.get("allow_spot", True)))
            new_config["allow_futures"] = c2.checkbox("允许U本位永续自动交易", value=bool(auto_config.get("allow_futures", True)))
            market_options = [m for m, enabled in [("spot", new_config["allow_spot"]), ("futures", new_config["allow_futures"])] if enabled] or ["spot"]
            current_market = str(auto_config.get("default_market_type", "futures"))
            new_config["default_market_type"] = st.selectbox("默认市场", market_options, index=market_options.index(current_market) if current_market in market_options else 0, format_func=lambda x: "现货 Spot" if x == "spot" else "U本位永续合约")
            new_config["max_leverage"] = st.slider("最大允许杠杆", 1, 125, int(auto_config.get("max_leverage", 20) or 20))
            new_config["default_leverage"] = st.slider("指定执行杠杆", 1, int(new_config["max_leverage"]), min(int(auto_config.get("default_leverage", 5) or 5), int(new_config["max_leverage"])))
            new_config["take_profit_pct"] = st.number_input("止盈阈值 %（默认约为模拟交易2/3）", min_value=0.1, max_value=20.0, value=float(auto_config.get("take_profit_pct", 2.13) or 2.13), step=0.1)
            new_config["stop_loss_pct"] = st.number_input("止损阈值 %（默认约为模拟交易2/3）", min_value=-20.0, max_value=-0.1, value=float(auto_config.get("stop_loss_pct", -1.07) or -1.07), step=0.1)
            new_config["live_auto_order_enabled"] = st.checkbox("允许自动真实下单", value=bool(auto_config.get("live_auto_order_enabled")))
            new_config["live_auto_exit_enabled"] = st.checkbox("允许自动止盈止损", value=bool(auto_config.get("live_auto_exit_enabled")))
            if st.form_submit_button("保存自动交易参数", use_container_width=True):
                save_live_auto_config(new_config)
                st.success("自动交易参数已保存。")
                st.rerun()

    with tabs[6]:
        st.markdown("**自动交易统计**")
        render_metric_grid(
            [
                ("自动订单数", str(auto_review.get("auto_order_count", 0)), ""),
                ("成功数", str(auto_review.get("auto_success_count", 0)), "green"),
                ("失败数", str(auto_review.get("auto_failure_count", 0)), "red"),
                ("熔断次数", str(auto_review.get("circuit_breaker_count", 0)), "red"),
            ]
        )
        audit = load_live_auto_audit_log(100)
        if not audit:
            st.info("暂无自动交易事件日志。")
        for event in audit:
            st.caption(f"{event.get('time')}｜{event.get('event')}｜{event.get('symbol')}｜{event.get('result')}｜{event.get('reason')}")


def render_trade_records_page() -> None:
    """模拟交易持久化记录与统计中心。"""
    render_page_head("trade_records")
    init_database()
    stats = get_persistent_sim_trade_stats()
    st.markdown(
        """
        <div class="app-shell">
          <div class="module-card">
            <div class="module-title">模拟交易数据持久化系统</div>
            <div class="module-desc">开仓和平仓会写入 SQLite，本地程序重启后交易记录仍会保留。这里展示的是持久化数据库记录，不是临时页面缓存。</div>
          </div>
        </div>
        """,
        unsafe_allow_html=True,
    )
    render_metric_grid(
        [
            ("总交易数", str(stats.get("total_trades", 0)), "blue"),
            ("盈利次数", str(stats.get("win_count", 0)), "green"),
            ("亏损次数", str(stats.get("loss_count", 0)), "red"),
            ("胜率", f"{float(stats.get('win_rate', 0) or 0):.2f}%", "green" if float(stats.get("win_rate", 0) or 0) >= 50 else "yellow"),
            ("总收益", f"{float(stats.get('total_pnl', 0) or 0):+.2f} USDT", "green" if float(stats.get("total_pnl", 0) or 0) >= 0 else "red"),
            ("平均收益", f"{float(stats.get('average_pnl', 0) or 0):+.2f} USDT", ""),
            ("最大盈利", f"{float(stats.get('max_profit', 0) or 0):+.2f} USDT", "green"),
            ("最大亏损", f"{float(stats.get('max_loss', 0) or 0):+.2f} USDT", "red"),
            ("最大回撤", f"{float(stats.get('max_drawdown', 0) or 0):.2f} USDT", "yellow"),
            ("连续盈利", str(stats.get("max_win_streak", 0)), "green"),
            ("连续亏损", str(stats.get("max_loss_streak", 0)), "red"),
            ("当前持仓", str(stats.get("current_open_positions", 0)), "yellow"),
            ("累计开仓", str(stats.get("cumulative_opens", 0)), "blue"),
            ("数据库", "已连接", "green"),
        ]
    )
    tabs = st.tabs(["最近交易", "自动复盘", "数据库状态"])
    with tabs[0]:
        c1, c2, c3 = st.columns([2, 1, 1])
        search = c1.text_input("搜索交易记录", placeholder="输入币种、方向、策略或平仓原因")
        status = c2.selectbox("状态", ["全部", "OPEN", "CLOSED"])
        page_size = c3.selectbox("每页数量", [20, 50, 100], index=0)
        page_no = max(1, int(st.number_input("页码", min_value=1, value=1, step=1)))
        rows = query_sim_trades(limit=int(page_size), offset=(page_no - 1) * int(page_size), search=search, status="" if status == "全部" else status)
        table = [
            {
                "时间": row.get("close_time") or row.get("open_time"),
                "币种": row.get("symbol"),
                "方向": row.get("side"),
                "开仓价": row.get("entry_price"),
                "平仓价": row.get("exit_price") or "",
                "收益%": f"{float(row.get('pnl_percent', 0) or 0):+.2f}%" if row.get("status") == "CLOSED" else "",
                "盈亏USDT": f"{float(row.get('pnl', 0) or 0):+.4f}" if row.get("status") == "CLOSED" else "",
                "持仓时间": f"{float(row.get('holding_minutes', 0) or 0):.1f}分钟" if row.get("status") == "CLOSED" else "持仓中",
                "状态": row.get("status"),
                "策略": row.get("strategy") or "",
            }
            for row in rows
        ]
        if table:
            st.dataframe(table, use_container_width=True, hide_index=True)
            render_kline_jump_links([row.get("symbol") for row in rows], "本页交易对象K线")
        else:
            st.info("暂无符合条件的模拟交易记录。开启自动模拟并产生开/平仓后会写入这里。")
    with tabs[1]:
        reviews = query_review_records(100)
        if not reviews:
            st.info("暂无自动复盘记录。每次模拟完整平仓后会自动生成。")
        for row in reviews[:100]:
            with st.expander(f"{row.get('symbol')}｜{row.get('side')}｜{row.get('close_time')}｜{float(row.get('pnl', 0) or 0):+.4f} USDT"):
                st.markdown(kline_symbol_link(row.get("symbol"), f"查看 {row.get('symbol')} K线", "watch-pill"), unsafe_allow_html=True)
                st.markdown(f"**开仓原因**：{row.get('open_reason') or '暂无'}")
                st.markdown(f"**市场结构**：{row.get('market_structure') or '暂无'}")
                st.markdown(f"**AI评分**：{row.get('ai_score') or 0}")
                st.markdown(f"**交易逻辑**：{row.get('trade_logic') or '暂无'}")
                st.markdown(f"**持仓时间**：{float(row.get('holding_minutes', 0) or 0):.1f} 分钟")
                st.markdown(f"**平仓原因**：{row.get('close_reason') or '暂无'}")
                st.markdown(f"**最终收益**：{float(row.get('pnl', 0) or 0):+.4f} USDT / {float(row.get('pnl_percent', 0) or 0):+.2f}%")
    with tabs[2]:
        st.caption(f"SQLite 数据库路径：{stats.get('database_path')}")
        st.info("数据库会在程序启动或首次读写时自动创建。重置模拟账户不会删除 SQLite 历史记录。")


def render_operations_center() -> None:
    """系统运维中心，兼容本地Windows与Ubuntu/systemd部署。"""
    status = get_system_operations_status()
    external_status = get_external_ai_status()
    gemini_status = external_status.get("gemini") or {}
    gemini_text = "API Key缺失"
    if gemini_status.get("configured"):
        gemini_text = "正常" if not gemini_status.get("last_error") else str(gemini_status.get("last_error"))[:18]
    render_metric_grid(
        [
            ("服务器状态", str(status.get("server_status", "-")), "green"),
            ("AI模型状态", str(status.get("ai_model_status", "-")), "green" if str(status.get("ai_model_status")) == "active" else "yellow"),
            ("GitHub版本", str(status.get("github_version", "-")), "blue"),
            ("运行时间", str(status.get("uptime", "-")), "green"),
            ("CPU占用", str(status.get("cpu", "-")), "yellow"),
            ("内存占用", str(status.get("memory", "-")), "yellow"),
            ("磁盘状态", str(status.get("disk", "-")), "blue"),
            ("运行环境", str(status.get("platform", "-"))[:32], ""),
            ("Gemini", gemini_text, "green" if gemini_text == "正常" else "yellow"),
        ]
    )
    st.caption("Vultr 东京 / Ubuntu 24.04 / systemd 部署时，下方按钮会调用 aimodel 服务。本地 Windows 只显示状态，不执行 systemctl。")
    c1, c2, c3, c4, c5 = st.columns(5)
    actions = [
        (c1, "刷新状态", "status"),
        (c2, "重启AI模型", "restart"),
        (c3, "停止AI模型", "stop"),
        (c4, "启动AI模型", "start"),
        (c5, "GitHub同步更新", "update"),
    ]
    for col, label, action in actions:
        if col.button(label, use_container_width=True):
            st.session_state["ops_command_result"] = run_aimodel_control(action)
    result = st.session_state.get("ops_command_result")
    if result:
        if result.get("ok"):
            st.success(result.get("stdout") or "操作已执行。")
        else:
            st.warning(result.get("stderr") or result.get("stdout") or "操作未执行。")
    st.markdown("**日志中心**")
    f1, f2 = st.columns([2, 1])
    keyword = f1.text_input("搜索日志", placeholder="输入关键词")
    level = f2.selectbox("日志级别", ["全部", "INFO", "WARNING", "ERROR"])
    logs = load_recent_log_lines(100, keyword=keyword, level=level)
    if not logs:
        st.info("暂无符合条件的日志。")
    else:
        st.dataframe(logs, use_container_width=True, hide_index=True)


def render_binance_data_status_center(symbol: str | None = None) -> None:
    """Binance实时数据诊断中心。"""
    current_symbol = str(symbol or st.session_state.get("current_symbol") or "BTCUSDT").upper().strip()
    snapshot = market_cache.snapshot()
    st.markdown("**Binance 实时数据状态中心**")
    render_metric_grid(
        [
            ("Binance连接", str(snapshot.get("binance_status", "初始化")), "green" if snapshot.get("binance_status") == "在线" else "yellow"),
            ("REST状态", "正常" if snapshot.get("binance_status") == "在线" else "重试中", "green" if snapshot.get("binance_status") == "在线" else "yellow"),
            ("WebSocket状态", "REST回退模式", "blue"),
            ("Ticker更新", str(snapshot.get("last_update_time", "初始化中")), "green"),
            ("K线状态", str(snapshot.get("kline_status", "初始化")), "green" if snapshot.get("kline_status") in {"实时", "在线"} else "yellow"),
            ("K线更新", str(snapshot.get("kline_last_update_time", "初始化中")), "green"),
            ("盘口状态", str(snapshot.get("orderbook_status", "初始化")), "green" if snapshot.get("orderbook_status") in {"实时", "在线"} else "yellow"),
            ("最近错误", str(snapshot.get("last_error") or snapshot.get("kline_last_error") or snapshot.get("orderbook_last_error") or "无")[:32], "red" if snapshot.get("last_error") or snapshot.get("kline_last_error") or snapshot.get("orderbook_last_error") else "green"),
        ]
    )
    c1, c2 = st.columns(2)
    if c1.button("运行 Binance 诊断", use_container_width=True):
        st.session_state["binance_diagnostics_result"] = run_binance_diagnostics(current_symbol)
    if c2.button("刷新当前交易对象数据", use_container_width=True):
        refresh_symbol_now(current_symbol)
        refresh_klines_now(current_symbol, market_cache.get_kline_interval())
        market_cache.request_orderbook_refresh()
        st.success(f"已请求刷新 {current_symbol} 的Ticker、K线和盘口。")
    diag = st.session_state.get("binance_diagnostics_result") or load_last_diagnostics()
    if diag:
        st.caption(f"诊断时间：{diag.get('time')}｜交易对象：{diag.get('symbol')}｜总体状态：{diag.get('status')}")
        checks = [
            {
                "项目": item.get("name"),
                "状态": item.get("status"),
                "耗时ms": item.get("elapsed_ms"),
                "错误原因": item.get("error") or "",
                "样例": item.get("sample") or "",
            }
            for item in diag.get("checks", [])
        ]
        st.dataframe(checks, use_container_width=True, hide_index=True)
        if diag.get("recent_error"):
            st.warning(f"最近错误：{diag.get('recent_error')}")
    logs = load_recent_binance_logs(100)
    recent_errors = [row for row in logs if str(row.get("level", "")).upper() in {"WARNING", "ERROR"}][:10]
    with st.expander("最近10条实时数据错误", expanded=bool(recent_errors)):
        if recent_errors:
            st.dataframe(
                [
                    {
                        "时间": row.get("time"),
                        "级别": row.get("level"),
                        "模块/接口": row.get("path"),
                        "交易对象": row.get("symbol"),
                        "状态": row.get("result"),
                        "错误原因": row.get("reason"),
                        "耗时ms": row.get("elapsed_ms"),
                    }
                    for row in recent_errors
                ],
                use_container_width=True,
                hide_index=True,
            )
        else:
            st.success("最近没有实时数据错误。")
    with st.expander("最近100条 Binance 请求日志", expanded=False):
        if logs:
            st.dataframe(logs, use_container_width=True, hide_index=True)
        else:
            st.info("暂无 Binance 请求日志。")


def render_server_health_page() -> None:
    """服务器部署与长期运行优化中心。"""
    render_page_head("server")
    health = get_server_health()
    metrics = health.get("metrics") or {}
    backup = health.get("backup") or {}
    st.markdown(
        """
        <div class="app-shell">
          <div class="module-card warning-box">
            <b>服务器长期运行提示</b><br>
            服务器启动默认进入 READ_ONLY。Live Manual 与 LIVE_AUTO_PILOT 不会在重启后自动恢复，真实交易必须重新人工确认。
          </div>
        </div>
        """,
        unsafe_allow_html=True,
    )
    render_metric_grid(
        [
            ("运行状态", str(health.get("status", "运行中")), "green" if health.get("status") == "运行中" else "red" if health.get("status") == "异常" else "yellow"),
            ("当前模式", str(health.get("mode", "READ_ONLY")), "yellow"),
            ("启动时间", str(health.get("start_time", "-")), ""),
            ("运行时长", str(health.get("uptime", "-")), "blue"),
            ("心跳时间", str(health.get("last_heartbeat_time", "-")), "green"),
            ("日志目录", "正常" if health.get("logs_writable") else "不可写", "green" if health.get("logs_writable") else "red"),
            ("数据目录", "正常" if health.get("data_writable") else "不可写", "green" if health.get("data_writable") else "red"),
            ("磁盘剩余", f"{float(metrics.get('disk_free_gb', 0) or 0):.2f} GB / {float(metrics.get('disk_free_pct', 0) or 0):.1f}%", "yellow"),
        ]
    )
    tabs = st.tabs(["健康总览", "实时数据诊断", "安全启动", "备份与日志", "部署说明", "访问安全", "系统运维中心"])

    with tabs[0]:
        render_metric_grid(
            [
                ("Python", str(health.get("python_version", "-")), ""),
                ("系统", str(health.get("platform", "-"))[:26], ""),
                ("CPU", f"{metrics.get('cpu_percent')}%" if metrics.get("psutil_available") else "当前环境暂不支持", "yellow"),
                ("内存", f"{metrics.get('memory_percent')}%" if metrics.get("psutil_available") else "当前环境暂不支持", "yellow"),
                ("最近备份", str(backup.get("latest_backup_time") or "未备份"), "green" if backup.get("latest_ok") else "yellow"),
                ("systemd", str(health.get("systemd_status", "-"))[:26], "yellow"),
            ]
        )
        st.markdown("**配置检查**")
        for item in (health.get("config") or {}).get("checks", []):
            if item.get("ok"):
                st.success(f"{item.get('name')}：{item.get('message')}")
            else:
                st.warning(f"{item.get('name')}：{item.get('message')}")
        st.markdown("**最近安全降级事件**")
        events = health.get("recent_safety_events") or []
        if not events:
            st.info("暂无运行时安全降级事件。")
        for event in events[:10]:
            st.caption(f"{event.get('time')}｜{event.get('event')}｜{event.get('risk_level')}｜{event.get('reason')}")

    with tabs[1]:
        render_binance_data_status_center(str(st.session_state.get("current_symbol") or "BTCUSDT"))

    with tabs[2]:
        st.markdown("**服务器安全启动规则**")
        st.info("点击下方按钮会执行安全启动降级：关闭 Live Manual、关闭 LIVE_AUTO_PILOT、保持 READ_ONLY。")
        if st.button("执行服务器安全启动检查", use_container_width=True):
            st.session_state["safe_startup_result"] = apply_safe_startup(force=True)
            st.rerun()
        result = st.session_state.get("safe_startup_result")
        if result:
            st.success(result.get("message", "服务器启动完成，当前模式：READ_ONLY。真实交易未自动开启。"))
            for action in result.get("actions", []):
                st.caption(action)
        st.markdown("**最近重启记录**")
        restarts = health.get("recent_restart") or []
        if not restarts:
            st.info("暂无重启记录。")
        for event in restarts:
            st.caption(f"{event.get('time')}｜{event.get('event')}｜恢复模式：{event.get('restored_mode')}｜{event.get('message')}")

    with tabs[3]:
        st.markdown("**备份计划**")
        backup_settings = load_backup_settings()
        with st.form("backup_settings_form"):
            backup_settings["auto_daily_backup"] = st.checkbox("每日自动备份（预留）", value=bool(backup_settings.get("auto_daily_backup")))
            backup_settings["auto_weekly_backup"] = st.checkbox("每周完整备份（预留）", value=bool(backup_settings.get("auto_weekly_backup")))
            backup_settings["include_config"] = st.checkbox("备份 config", value=bool(backup_settings.get("include_config", True)))
            backup_settings["include_data"] = st.checkbox("备份 data", value=bool(backup_settings.get("include_data", True)))
            backup_settings["include_logs"] = st.checkbox("备份 logs", value=bool(backup_settings.get("include_logs", True)))
            if st.form_submit_button("保存备份设置", use_container_width=True):
                save_backup_settings(backup_settings)
                st.success("备份设置已保存。")
        b1, b2 = st.columns(2)
        if b1.button("立即手动备份", use_container_width=True):
            st.session_state["server_backup_result"] = create_backup("manual")
        if b2.button("执行日志轮转", use_container_width=True):
            st.session_state["server_rotation_result"] = rotate_logs()
        backup_result = st.session_state.get("server_backup_result")
        if backup_result:
            (st.success if backup_result.get("ok") else st.error)(f"备份文件：{backup_result.get('zip_file')}｜文件数：{backup_result.get('file_count')}｜大小：{backup_result.get('size_bytes')} bytes")
        rotation_result = st.session_state.get("server_rotation_result")
        if rotation_result:
            st.success(f"日志轮转完成：归档 {rotation_result.get('archived_count')} 个文件。")
        st.markdown("**最近备份状态**")
        st.caption(f"manifest：{backup.get('latest_manifest') or '暂无'}")
        st.caption(f"时间：{backup.get('latest_backup_time') or '暂无'}｜状态：{'成功' if backup.get('latest_ok') else '暂无或失败'}｜大小：{backup.get('latest_size_bytes', 0)} bytes")

    with tabs[4]:
        git_status = get_git_status()
        st.markdown("**部署目录规范**")
        st.code(
            """/opt/ai_model/
app.py
requirements.txt
.env
.env.example
config/
data/
logs/
backups/
scripts/
docs/
runtime/
venv/""",
            language="text",
        )
        st.markdown("**文档与脚本**")
        st.caption("部署文档：docs/DEPLOY_SERVER.md")
        st.caption("远程访问：docs/REMOTE_ACCESS.md")
        st.caption("systemd 示例：docs/ai_model.service.example")
        st.caption("启动脚本：scripts/start_server.sh / scripts/start_local.bat")
        st.caption("更新脚本：scripts/update_from_git.sh")
        st.markdown(f"Git状态：{git_status.get('message')}")
        st.info("远程更新默认不自动执行，必须用户手动运行脚本。")

    with tabs[5]:
        server_settings = load_server_settings()
        env = (health.get("config") or {}).get("env") or {}
        configured = env.get("configured") or {}
        render_metric_grid(
            [
                (".env", "存在" if env.get("env_exists") else "缺少", "green" if env.get("env_exists") else "yellow"),
                ("Binance Key", "已配置" if configured.get("BINANCE_API_KEY") else "未配置", "green" if configured.get("BINANCE_API_KEY") else "yellow"),
                ("Binance Secret", "已配置" if configured.get("BINANCE_API_SECRET") else "未配置", "green" if configured.get("BINANCE_API_SECRET") else "yellow"),
                ("DeepSeek", "已配置" if configured.get("DEEPSEEK_API_KEY") else "未配置", "green" if configured.get("DEEPSEEK_API_KEY") else "yellow"),
                ("Gemini", "已配置" if configured.get("GEMINI_API_KEY") else "未配置", "green" if configured.get("GEMINI_API_KEY") else "yellow"),
                ("访问密码", "已设置" if configured.get("APP_ACCESS_PASSWORD") else "未设置", "green" if configured.get("APP_ACCESS_PASSWORD") else "yellow"),
            ]
        )
        with st.form("server_auth_settings_form"):
            server_settings["enable_simple_auth"] = st.checkbox("开启简单访问密码（读取 ENABLE_SIMPLE_AUTH / APP_ACCESS_PASSWORD）", value=bool(server_settings.get("enable_simple_auth")))
            server_settings["restore_sim_auto_on_restart"] = st.checkbox("重启后允许恢复自动模拟（不影响实盘）", value=bool(server_settings.get("restore_sim_auto_on_restart")))
            if st.form_submit_button("保存访问安全设置", use_container_width=True):
                save_server_settings(server_settings)
                st.success("访问安全设置已保存。正式公网部署建议开启访问密码。")
        st.warning("页面不会显示 Secret，也不会把 Secret 写入日志。公网部署前请配置访问密码或反向代理认证。")

    with tabs[6]:
        render_operations_center()


def render_remote_control_page() -> None:
    """多设备远程控制 + 通知提醒系统。"""
    render_page_head("remote")
    device = ensure_current_device()
    device_id = str(device.get("device_id", ""))
    remote_status = get_remote_control_status(device_id)
    live_status = get_live_safety_status()
    live_settings = live_status.get("settings") or {}
    auto_status = get_live_auto_status()
    server_health = get_server_health()
    notif_summary = get_notification_summary()
    st.markdown(
        """
        <div class="app-shell">
          <div class="module-card warning-box">
            <b>远程控制安全提示</b><br>
            远程控制不能绕过风控、审批、确认短句、安全锁或熔断。通知只提醒，不会直接执行交易。
          </div>
        </div>
        """,
        unsafe_allow_html=True,
    )
    render_metric_grid(
        [
            ("当前设备", str(device.get("device_name", "当前设备")), "blue"),
            ("权限", str(remote_status.get("permission_level", "admin")), "yellow"),
            ("服务器", str(server_health.get("status", "运行中")), "green" if server_health.get("status") == "运行中" else "red"),
            ("当前模式", str(live_settings.get("mode", "read_only")), "yellow"),
            ("安全锁", "已开启" if live_settings.get("kill_switch_enabled") else "未开启", "red" if live_settings.get("kill_switch_enabled") else "green"),
            ("自动实盘", "开启" if auto_status.get("enabled") else "关闭", "green" if auto_status.get("enabled") else "yellow"),
            ("自动熔断", "已触发" if auto_status.get("circuit_breaker_enabled") else "未触发", "red" if auto_status.get("circuit_breaker_enabled") else "green"),
            ("未读通知", str(notif_summary.get("unread_count", 0)), "red" if notif_summary.get("unread_count") else "green"),
        ]
    )
    tabs = st.tabs(["远程控制", "通知中心", "设备管理", "通知规则", "远程审计"])

    with tabs[0]:
        st.markdown("**远程控制面板**")
        st.caption("真实订单仍必须进入实盘页面和确认流程；这里不能直接下单。")
        phrase = st.text_input("敏感操作确认短句", type="password", placeholder="按操作提示输入对应确认短句")
        c1, c2, c3 = st.columns(3)
        if c1.button("暂停自动实盘试运行", use_container_width=True):
            perm = check_permission("pause_auto_live", device_id)
            if perm.get("ok"):
                result = pause_live_auto_pilot("远程控制中心暂停自动实盘试运行。")
                create_notification({"type": "auto_live", "priority": "高", "title": "自动实盘已远程暂停", "message": result.get("message", ""), "source": "remote"})
            else:
                result = perm
            record_remote_action({"device_id": device_id, "device_name": device.get("device_name"), "permission_level": perm.get("permission_level"), "action_type": "pause_auto_live", "page": "remote", "success": result.get("ok"), "reason": result.get("message"), "current_mode": live_settings.get("mode"), "safety_lock_status": live_settings.get("kill_switch_enabled")})
            (st.success if result.get("ok") else st.error)(result.get("message"))
            st.rerun()
        if c2.button("关闭自动实盘试运行", use_container_width=True):
            perm = check_permission("pause_auto_live", device_id)
            if perm.get("ok"):
                result = disable_live_auto_pilot("远程控制中心关闭自动实盘试运行。")
                create_notification({"type": "auto_live", "priority": "高", "title": "自动实盘已远程关闭", "message": result.get("message", ""), "source": "remote"})
            else:
                result = perm
            record_remote_action({"device_id": device_id, "device_name": device.get("device_name"), "permission_level": perm.get("permission_level"), "action_type": "disable_auto_live", "page": "remote", "success": result.get("ok"), "reason": result.get("message"), "current_mode": live_settings.get("mode"), "safety_lock_status": live_settings.get("kill_switch_enabled")})
            (st.success if result.get("ok") else st.error)(result.get("message"))
            st.rerun()
        if c3.button("进入只读安全模式", use_container_width=True):
            perm = check_permission("sim_control", device_id)
            if perm.get("ok"):
                pause_live_auto_pilot("远程进入只读安全模式。")
                result = trigger_live_kill_switch("远程控制中心请求进入只读安全模式。")
                create_notification({"type": "server", "priority": "高", "title": "系统已进入远程只读安全模式", "message": "已暂停自动实盘并触发实盘安全锁。", "source": "remote"})
            else:
                result = perm
            record_remote_action({"device_id": device_id, "device_name": device.get("device_name"), "permission_level": perm.get("permission_level"), "action_type": "read_only_mode", "page": "remote", "success": result.get("ok"), "reason": result.get("message"), "current_mode": live_settings.get("mode"), "safety_lock_status": live_settings.get("kill_switch_enabled")})
            (st.success if result.get("ok") else st.error)(result.get("message"))
            st.rerun()

        st.markdown("**紧急远程操作**")
        e1, e2 = st.columns(2)
        if e1.button("触发紧急停止 / 安全锁", use_container_width=True):
            perm = check_permission("emergency_stop", device_id)
            confirm = require_confirmation("emergency_stop", phrase)
            if perm.get("ok") and confirm.get("ok"):
                pause_live_auto_pilot("远程紧急停止。")
                trigger_live_auto_circuit_breaker("远程紧急停止触发自动实盘熔断。")
                result = trigger_live_kill_switch("远程控制中心触发紧急停止。")
                create_notification({"type": "server", "priority": "紧急", "title": "远程紧急停止已触发", "message": "系统已阻止新的实盘和自动实盘操作。", "source": "remote"})
            else:
                result = {"ok": False, "message": perm.get("message") if not perm.get("ok") else confirm.get("message")}
            record_remote_action({"device_id": device_id, "device_name": device.get("device_name"), "permission_level": perm.get("permission_level"), "action_type": "emergency_stop", "page": "remote", "success": result.get("ok"), "reason": result.get("message"), "second_confirmed": True, "confirm_phrase_ok": confirm.get("ok"), "current_mode": live_settings.get("mode"), "safety_lock_status": live_settings.get("kill_switch_enabled")})
            (st.success if result.get("ok") else st.error)(result.get("message"))
            st.rerun()
        if e2.button("解除自动实盘熔断", use_container_width=True):
            perm = check_permission("release_lock", device_id)
            confirm = require_confirmation("release_auto_circuit", phrase)
            if perm.get("ok") and confirm.get("ok"):
                result = release_live_auto_circuit_breaker("我确认解除自动实盘熔断")
                create_notification({"type": "auto_live", "priority": "高", "title": "自动实盘熔断已远程解除", "message": result.get("message", ""), "source": "remote"})
            else:
                result = {"ok": False, "message": perm.get("message") if not perm.get("ok") else confirm.get("message")}
            record_remote_action({"device_id": device_id, "device_name": device.get("device_name"), "permission_level": perm.get("permission_level"), "action_type": "release_auto_circuit", "page": "remote", "success": result.get("ok"), "reason": result.get("message"), "second_confirmed": True, "confirm_phrase_ok": confirm.get("ok"), "current_mode": live_settings.get("mode"), "safety_lock_status": live_settings.get("kill_switch_enabled")})
            (st.success if result.get("ok") else st.error)(result.get("message"))
            st.rerun()

    with tabs[1]:
        st.markdown("**通知中心**")
        f1, f2 = st.columns(2)
        status_filter = f1.selectbox("状态筛选", ["unread", "read", "archived", "resolved", "全部"], format_func=lambda x: {"unread": "未读", "read": "已读", "archived": "已归档", "resolved": "已解决", "全部": "全部"}[x])
        type_filter = f2.selectbox("类型筛选", ["全部", "approval", "risk", "server", "live", "auto_live", "sim", "external_ai"])
        if st.button("全部标记已读", use_container_width=True):
            result = mark_all_notifications_read()
            st.success(result.get("message"))
            st.rerun()
        rows = load_notifications(None if status_filter == "全部" else status_filter, 300)
        if type_filter != "全部":
            rows = [row for row in rows if row.get("type") == type_filter]
        if not rows:
            st.info("当前暂无通知。")
        for row in rows:
            color = "red" if row.get("priority") in {"高", "紧急"} else "yellow" if row.get("priority") == "中" else "blue"
            with st.expander(f"{row.get('priority')}｜{row.get('title')}｜{row.get('status')}｜{row.get('created_time')}", expanded=row.get("status") == "unread" and row.get("priority") in {"高", "紧急"}):
                st.markdown(f"**类型**：{row.get('type')}｜**来源**：{row.get('source')}｜**交易对象**：{row.get('symbol') or '-'}")
                st.markdown(str(row.get("message", "")).replace("\n", "  \n"))
                render_metric_grid([
                    ("优先级", str(row.get("priority")), color),
                    ("合并次数", str(row.get("merge_count", 1)), "yellow"),
                    ("需要关注", "是" if row.get("requires_attention") else "否", "red" if row.get("requires_attention") else "green"),
                    ("关联ID", str(row.get("related_id") or "-"), ""),
                ])
                n1, n2, n3 = st.columns(3)
                if n1.button("标记已读", key=f"read_{row.get('notification_id')}", use_container_width=True):
                    mark_notification_read(str(row.get("notification_id")))
                    st.rerun()
                if n2.button("归档", key=f"archive_{row.get('notification_id')}", use_container_width=True):
                    archive_notification(str(row.get("notification_id")))
                    st.rerun()
                target_page = ((row.get("actions") or [{}])[0] or {}).get("page")
                if n3.button("进入相关页面", key=f"go_{row.get('notification_id')}", disabled=not bool(target_page), use_container_width=True):
                    st.query_params["page"] = target_page
                    st.rerun()

    with tabs[2]:
        st.markdown("**设备管理**")
        name = st.text_input("当前设备名称", value=str(device.get("device_name", "当前设备")))
        if st.button("保存当前设备名称", use_container_width=True):
            st.session_state["device_name"] = name
            register_device({**device, "device_name": name})
            st.success("设备名称已保存。")
            st.rerun()
        devices = load_registered_devices()
        if not devices:
            st.info("暂无设备记录。")
        for item in devices:
            st.markdown(
                f"""
                <div class="status-card">
                  <b>{escape(str(item.get("device_name", "-")))}</b>｜{escape(str(item.get("device_id", "-")))}<br>
                  权限：{escape(str(item.get("permission_level", "admin")))}｜可信：{"是" if item.get("trusted") else "否"}｜允许远控：{"是" if item.get("allow_remote_control") else "否"}<br>
                  首次：{escape(str(item.get("first_seen_time", "-")))}｜最近：{escape(str(item.get("last_seen_time", "-")))}｜页面：{escape(str(item.get("last_page", "-")))}
                </div>
                """,
                unsafe_allow_html=True,
            )
        st.markdown("**活跃会话**")
        for sess in remote_status.get("active_sessions") or []:
            st.caption(f"{sess.get('session_id')}｜设备 {sess.get('device_id')}｜权限 {sess.get('permission_level')}｜最近 {sess.get('last_seen_time')}｜页面 {sess.get('last_page')}")

    with tabs[3]:
        st.markdown("**通知规则配置**")
        rules = get_notification_rules()
        with st.form("notification_rules_form"):
            rules["approval_notifications"] = st.checkbox("通知审批单", value=bool(rules.get("approval_notifications", True)))
            rules["risk_notifications"] = st.checkbox("通知高风险", value=bool(rules.get("risk_notifications", True)))
            rules["live_notifications"] = st.checkbox("通知实盘事件", value=bool(rules.get("live_notifications", True)))
            rules["sim_notifications"] = st.checkbox("通知普通模拟事件", value=bool(rules.get("sim_notifications", False)))
            rules["server_notifications"] = st.checkbox("通知服务器异常", value=bool(rules.get("server_notifications", True)))
            rules["auto_live_notifications"] = st.checkbox("通知自动实盘事件", value=bool(rules.get("auto_live_notifications", True)))
            rules["external_ai_notifications"] = st.checkbox("通知外部AI异常", value=bool(rules.get("external_ai_notifications", True)))
            rules["only_high_priority"] = st.checkbox("仅通知高优先级", value=bool(rules.get("only_high_priority", False)))
            rules["mute_low_priority"] = st.checkbox("静音低优先级", value=bool(rules.get("mute_low_priority", True)))
            rules["dedupe_minutes"] = st.number_input("同类通知合并窗口分钟", min_value=1, max_value=120, value=int(rules.get("dedupe_minutes", 5) or 5), step=1)
            rules["retention_days"] = st.number_input("通知保留天数", min_value=7, max_value=365, value=int(rules.get("retention_days", 90) or 90), step=1)
            st.markdown("**外部通知渠道预留**")
            channels = dict(rules.get("channels") or {})
            channels["system"] = st.checkbox("系统内通知", value=bool(channels.get("system", True)))
            channels["telegram"] = st.checkbox("Telegram Bot 预留", value=bool(channels.get("telegram", False)))
            channels["email"] = st.checkbox("邮件通知预留", value=bool(channels.get("email", False)))
            channels["webhook"] = st.checkbox("Webhook 预留", value=bool(channels.get("webhook", False)))
            channels["push"] = st.checkbox("手机 Push 预留", value=bool(channels.get("push", False)))
            channels["wechat"] = st.checkbox("微信通知预留", value=bool(channels.get("wechat", False)))
            rules["channels"] = channels
            if st.form_submit_button("保存通知规则", use_container_width=True):
                save_notification_rules(rules)
                st.success("通知规则已保存。外部渠道为预留，不会影响主系统。")
                st.rerun()
        if st.button("生成测试通知", use_container_width=True):
            create_notification({"type": "server", "priority": "中", "title": "测试通知", "message": "系统内通知中心工作正常。", "source": "remote"})
            st.success("测试通知已生成。")
            st.rerun()

    with tabs[4]:
        st.markdown("**远程操作审计日志**")
        actions = remote_status.get("recent_actions") or []
        if not actions:
            st.info("暂无远程操作记录。")
        for row in actions:
            st.caption(f"{row.get('time')}｜{row.get('device_name')}｜{row.get('permission_level')}｜{row.get('action_type')}｜成功：{row.get('success')}｜确认：{row.get('confirm_phrase_ok')}｜{row.get('reason')}")


def _render_approval_card(approval: dict[str, Any], compact: bool = False) -> None:
    approval_id = str(approval.get("approval_id", ""))
    symbol = str(approval.get("symbol", "-"))
    status = str(approval.get("status", "-"))
    deep = approval.get("deepseek_snapshot") or {}
    gemini = approval.get("gemini_snapshot") or {}
    soft_warning = bool(deep.get("soft_veto") or gemini.get("soft_veto"))
    title = f"{symbol}｜{approval.get('approval_type')}｜{approval.get('side')}｜{status}｜{approval_id}"
    with st.expander(title, expanded=not compact):
        render_metric_grid(
            [
                ("状态", status, "yellow" if status == "pending" else "green" if status in {"approved", "executed"} else "red"),
                ("模式", str(approval.get("mode", "-")), "yellow"),
                ("来源", str(approval.get("source", "-")), ""),
                ("优先级", str(approval.get("priority", "-")), "red" if approval.get("priority") in {"高", "紧急"} else "yellow"),
                ("创建价格", f"{float(approval.get('price_at_create', 0) or 0):.8f}", ""),
                ("当前价格", f"{float(approval.get('current_price', 0) or 0):.8f}", ""),
                ("建议金额", f"{float(approval.get('system_suggested_amount', 0) or 0):.2f} USDT", ""),
                ("风控最大", f"{float(approval.get('risk_max_amount', 0) or 0):.2f} USDT", "red"),
            ]
        )
        st.markdown(
            f"""
            <div class="status-card">
              有效期：{escape(str(approval.get("expires_at", "-")))}｜
              用户选择金额：{escape(str(approval.get("user_selected_amount") or "未选择"))}<br>
              DeepSeek：{escape(str(deep.get("vote", "暂无")))}｜风险 {escape(str(deep.get("risk_level", "-")))}｜{escape(_safe_committee_text(deep.get("summary", "暂无")))}<br>
              Gemini：{escape(str(gemini.get("vote", "暂无")))}｜风险 {escape(str(gemini.get("risk_level", "-")))}｜{escape(_safe_committee_text(gemini.get("summary", "暂无")))}
            </div>
            """,
            unsafe_allow_html=True,
        )
        if soft_warning:
            st.warning("外部AI存在风险提醒。用户仍批准时，本次操作会记录为人工风险确认。")
        if status in {"pending", "modified"}:
            m1, m2, m3 = st.columns(3)
            new_amount = m1.number_input("修改用户金额", min_value=0.0, max_value=50.0, value=float(approval.get("user_selected_amount") or approval.get("system_suggested_amount") or 0), step=1.0, key=f"appr_amount_{approval_id}")
            new_price = m2.number_input("更新当前价格", min_value=0.0, value=float(approval.get("current_price") or approval.get("price_at_create") or 0), step=0.01, key=f"appr_price_{approval_id}")
            new_type = m3.selectbox("订单类型", ["LIMIT", "MARKET"], index=0 if approval.get("order_type") != "MARKET" else 1, key=f"appr_type_{approval_id}")
            c1, c2, c3 = st.columns(3)
            if c1.button("保存修改", key=f"modify_{approval_id}", use_container_width=True):
                result = modify_approval(approval_id, {"user_selected_amount": new_amount, "current_price": new_price, "order_type": new_type})
                (st.success if result.get("ok") else st.error)(result.get("message"))
                st.rerun()
            if c2.button("批准审批单", key=f"approve_{approval_id}", use_container_width=True):
                result = approve_approval(approval_id, {"user_selected_amount": new_amount, "reason": "用户在审批中心批准。"})
                (st.success if result.get("ok") else st.error)(result.get("message"))
                st.rerun()
            reject_reason = st.selectbox("拒绝原因", ["风险太高", "不想交易", "仓位太大", "信号不清晰", "外部AI反对", "手动取消", "其他"], key=f"reject_reason_{approval_id}")
            if c3.button("拒绝审批单", key=f"reject_{approval_id}", use_container_width=True):
                result = reject_approval(approval_id, reject_reason)
                (st.success if result.get("ok") else st.error)(result.get("message"))
                st.rerun()
        elif status == "approved":
            required_phrase = "我确认执行小资金实盘平仓" if approval.get("approval_type") in {"exit", "partial_exit"} else "我确认执行小资金实盘订单"
            if approval.get("approval_type") == "cancel":
                required_phrase = "我确认撤销该真实订单"
            st.markdown(f"**确认短句：** `{required_phrase}`")
            phrase = st.text_input("执行确认短句", key=f"approval_phrase_{approval_id}")
            if st.button("运行审批执行前检查", key=f"preflight_{approval_id}", use_container_width=True):
                current_price = float(approval.get("current_price") or approval.get("price_at_create") or 0)
                test_result = {}
                if approval.get("mode") == "LIVE_MANUAL" and approval.get("approval_type") == "entry":
                    plan = create_live_order_plan(approval.get("committee_snapshot") or {}, approval.get("entry_plan") or {})
                    test_result = run_spot_test_order(plan)
                elif approval.get("mode") == "LIVE_MANUAL" and approval.get("approval_type") in {"exit", "partial_exit"}:
                    test_result = run_exit_spot_test_order(approval.get("exit_plan") or {})
                st.session_state[f"approval_test_{approval_id}"] = test_result
                st.session_state[f"approval_preflight_{approval_id}"] = run_approval_preflight(approval, current_price, test_result, phrase)
            preflight = st.session_state.get(f"approval_preflight_{approval_id}") or {}
            if preflight:
                st.markdown(f"**检查结果：{preflight.get('message')}**")
                for item in preflight.get("checks", []):
                    (st.success if item.get("ok") else st.error)(f"{item.get('name')}：{item.get('message')}")
            if st.button("执行已批准审批单", key=f"execute_{approval_id}", disabled=not bool(preflight.get("ok")), use_container_width=True):
                result = execute_approved_approval(approval, st.session_state.get(f"approval_test_{approval_id}") or {}, phrase, approval.get("current_price"))
                (st.success if result.get("ok") else st.error)(result.get("message"))
                st.rerun()
        else:
            st.caption("该审批单当前状态不能执行。")
        history_html = "".join(
            f"<div>{escape(str(event.get('time', '')))}｜{escape(str(event.get('action', '')))}｜{escape(str(event.get('reason', '')))}</div>"
            for event in approval.get("approval_history", [])
        )
        st.markdown(f"<details class='status-card'><summary>审批历史</summary>{history_html or '暂无审批历史。'}</details>", unsafe_allow_html=True)


def render_api_external_interface_center() -> None:
    """我的页统一接口中心：交易所、外部AI、通知和审计。"""
    live_status = get_live_safety_status()
    live_settings = live_status.get("settings") or load_live_settings()
    live_credentials = live_status.get("credentials") or {}
    secure_status = get_secure_api_status()
    ai_settings = load_external_ai_settings()
    ai_status = get_external_ai_status()
    deepseek_secret = get_external_ai_secret_status("deepseek")
    gemini_secret = get_external_ai_secret_status("gemini")
    st.markdown(
        """
        <div class="app-shell">
          <div class="module-card warning-box">
            <b>API 与外部接口中心</b><br>
            所有密钥必须脱敏管理。手机端可直接填写并保存；保存后会自动保留到本机 .env，直到你下次重新填写或手动清除。
          </div>
        </div>
        """,
        unsafe_allow_html=True,
    )
    tabs = st.tabs(["交易所接口", "外部 AI 接口", "账户与登录接口", "通知接口", "安全与审计"])
    with tabs[0]:
        render_metric_grid(
            [
                ("Binance API", secure_status.get("BINANCE_API_KEY", {}).get("masked", "未配置"), "yellow"),
                ("Binance Secret", secure_status.get("BINANCE_API_SECRET", {}).get("secret_status", "未配置"), "green" if secure_status.get("BINANCE_API_SECRET", {}).get("configured") else "yellow"),
                ("Testnet API", secure_status.get("BINANCE_TESTNET_API_KEY", {}).get("masked", "未配置"), "yellow"),
                ("Testnet Secret", secure_status.get("BINANCE_TESTNET_API_SECRET", {}).get("secret_status", "未配置"), "green" if secure_status.get("BINANCE_TESTNET_API_SECRET", {}).get("configured") else "yellow"),
                ("当前模式", str(live_settings.get("mode", "read_only")), "yellow"),
                ("Live Manual", "默认禁用", "red"),
            ]
        )
        st.info("手机端填写后会自动保存到本机 .env，并立即在当前运行进程中生效。页面只显示脱敏状态；空输入表示保留原值。")
        with st.form("binance_secure_api_form"):
            b1, b2 = st.columns(2)
            binance_key = b1.text_input("Binance API Key", value="", type="password", placeholder="留空则不修改")
            binance_secret = b2.text_input("Binance API Secret", value="", type="password", placeholder="留空则不修改")
            t1, t2 = st.columns(2)
            testnet_key = t1.text_input("Binance Testnet API Key", value="", type="password", placeholder="留空则不修改")
            testnet_secret = t2.text_input("Binance Testnet API Secret", value="", type="password", placeholder="留空则不修改")
            st.caption("建议真实 Binance API 关闭提现权限，并开启 IP 白名单。")
            if st.form_submit_button("保存并启用交易所 API", use_container_width=True):
                result = write_secure_api_values(
                    {
                        "BINANCE_API_KEY": binance_key,
                        "BINANCE_API_SECRET": binance_secret,
                        "BINANCE_TESTNET_API_KEY": testnet_key,
                        "BINANCE_TESTNET_API_SECRET": testnet_secret,
                    }
                )
                (st.success if result.get("ok") else st.warning)(result.get("message"))
                st.rerun()
        with st.expander("清除交易所 API", expanded=False):
            clear_main = st.checkbox("清除 Binance 正式 API Key / Secret")
            clear_test = st.checkbox("清除 Binance Testnet API Key / Secret")
            confirm_clear = st.checkbox("我确认清除选中的交易所 API 密钥")
            if st.button("清除选中的交易所 API", disabled=not confirm_clear, use_container_width=True):
                keys: list[str] = []
                if clear_main:
                    keys.extend(["BINANCE_API_KEY", "BINANCE_API_SECRET"])
                if clear_test:
                    keys.extend(["BINANCE_TESTNET_API_KEY", "BINANCE_TESTNET_API_SECRET"])
                result = clear_secure_api_values(keys)
                (st.success if result.get("ok") else st.warning)(result.get("message"))
                st.rerun()
        if st.button("跳转查看实盘安全中心", use_container_width=True):
            st.query_params["page"] = "live"
            st.rerun()
    with tabs[1]:
        ssl_env = test_external_ai_ssl_environment()
        certifi_info = ssl_env.get("certifi") or {}
        render_metric_grid(
            [
                ("DeepSeek Key", deepseek_secret.get("masked_api_key", "未配置"), "yellow"),
                ("DeepSeek模式", str(ai_settings.get("deepseek", {}).get("mode", "shadow")), "blue"),
                ("DeepSeek成功率", f"{float((ai_status.get('deepseek') or {}).get('success_rate', 0) or 0):.1f}%", "green"),
                ("DeepSeek耗时", f"{float((ai_status.get('deepseek') or {}).get('avg_duration_ms', 0) or 0):.0f} ms", ""),
                ("Gemini Key", gemini_secret.get("masked_api_key", "未配置"), "yellow"),
                ("Gemini模式", str(ai_settings.get("gemini", {}).get("mode", "shadow")), "blue"),
                ("Gemini成功率", f"{float((ai_status.get('gemini') or {}).get('success_rate', 0) or 0):.1f}%", "green"),
                ("Gemini耗时", f"{float((ai_status.get('gemini') or {}).get('avg_duration_ms', 0) or 0):.0f} ms", ""),
                ("CA证书", "正常" if ssl_env.get("ok") else "异常", "green" if ssl_env.get("ok") else "red"),
                ("certifi", str(certifi_info.get("certifi_version", "未安装")), "green" if certifi_info.get("installed") else "red"),
            ]
        )
        st.caption(f"DeepSeek 最近调用：{(ai_status.get('deepseek') or {}).get('last_call_time', '暂无')}｜最近错误：{(ai_status.get('deepseek') or {}).get('last_error', '') or '无'}")
        st.caption(f"Gemini 最近调用：{(ai_status.get('gemini') or {}).get('last_call_time', '暂无')}｜最近错误：{(ai_status.get('gemini') or {}).get('last_error', '') or '无'}")
        with st.expander("SSL 证书环境", expanded=not bool(ssl_env.get("ok"))):
            st.markdown(f"CA证书状态：{'正常' if ssl_env.get('ok') else '异常'}")
            st.markdown(f"certifi路径：`{certifi_info.get('certifi_path', '未找到')}`")
            st.markdown(f"Python版本：`{ssl_env.get('python_version')}`｜requests：`{ssl_env.get('requests_version')}`｜urllib3：`{ssl_env.get('urllib3_version')}`")
            st.markdown(f"OpenSSL：`{ssl_env.get('openssl_version')}`｜系统时间：`{ssl_env.get('system_time')}`")
            if ssl_env.get("proxy_env"):
                st.warning("检测到代理环境变量，可能影响 SSL 证书验证。")
                st.json(ssl_env.get("proxy_env"))
            if ssl_env.get("warning"):
                st.warning(str(ssl_env.get("warning")))
            if ssl_env.get("suggestion"):
                st.info(str(ssl_env.get("suggestion")))
            if st.button("测试 SSL 证书环境", use_container_width=True):
                st.session_state["external_ai_ssl_env_test"] = test_external_ai_ssl_environment()
            if st.session_state.get("external_ai_ssl_env_test"):
                st.json(st.session_state["external_ai_ssl_env_test"])
        with st.form("external_ai_secure_key_form"):
            a1, a2 = st.columns(2)
            deepseek_key = a1.text_input("DeepSeek API Key", value="", type="password", placeholder="留空则不修改")
            gemini_key = a2.text_input("Gemini API Key", value="", type="password", placeholder="留空则不修改")
            st.caption("手机端填写后会自动保存并保留到下次修改；外部 AI 只读取脱敏交易摘要，API Key 不会发送给任何模型。空输入表示保留原值。")
            if st.form_submit_button("保存并启用外部 AI API Key", use_container_width=True):
                result = write_secure_api_values({"DEEPSEEK_API_KEY": deepseek_key, "GEMINI_API_KEY": gemini_key})
                (st.success if result.get("ok") else st.warning)(result.get("message"))
                st.rerun()
        with st.expander("清除外部 AI API Key", expanded=False):
            clear_deepseek = st.checkbox("清除 DeepSeek API Key")
            clear_gemini = st.checkbox("清除 Gemini API Key")
            confirm_ai_clear = st.checkbox("我确认清除选中的外部 AI API Key")
            if st.button("清除选中的外部 AI API Key", disabled=not confirm_ai_clear, use_container_width=True):
                keys = []
                if clear_deepseek:
                    keys.append("DEEPSEEK_API_KEY")
                if clear_gemini:
                    keys.append("GEMINI_API_KEY")
                result = clear_secure_api_values(keys)
                (st.success if result.get("ok") else st.warning)(result.get("message"))
                st.rerun()
        with st.form("external_ai_settings_form"):
            updated = dict(ai_settings)
            deep = dict(updated.get("deepseek", {}))
            gem = dict(updated.get("gemini", {}))
            deep["mode"] = st.selectbox("DeepSeek 当前模式", ["off", "shadow", "advisory"], index=["off", "shadow", "advisory"].index(str(deep.get("mode", "shadow"))) if str(deep.get("mode", "shadow")) in ["off", "shadow", "advisory"] else 1, format_func=lambda x: {"off": "关闭", "shadow": "正式投票模式", "advisory": "咨询模式预留"}[x])
            deep["base_url"] = st.text_input("DeepSeek Base URL", value=str(deep.get("base_url", "https://api.deepseek.com")))
            deep["model"] = st.text_input("DeepSeek 模型名称", value=str(deep.get("model", "deepseek-chat")))
            d1, d2, d3 = st.columns(3)
            deep["rate_limit_seconds"] = d1.number_input("DeepSeek限频秒数", min_value=10, max_value=600, value=int(deep.get("rate_limit_seconds", 60)), step=10)
            deep["timeout_seconds"] = d2.number_input("DeepSeek超时秒数", min_value=5, max_value=60, value=int(deep.get("timeout_seconds", 20)), step=5)
            deep["max_input_chars"] = d3.number_input("DeepSeek最大摘要长度", min_value=1000, max_value=20000, value=int(deep.get("max_input_chars", 6000)), step=500)
            deep["cache_enabled"] = st.checkbox("DeepSeek启用缓存", value=bool(deep.get("cache_enabled", True)))
            deep["show_in_committee"] = st.checkbox("DeepSeek参与委员会展示", value=bool(deep.get("show_in_committee", True)))
            deep["include_in_replay_stats"] = st.checkbox("DeepSeek参与复盘统计", value=bool(deep.get("include_in_replay_stats", True)))
            gem["mode"] = st.selectbox("Gemini 当前模式", ["off", "shadow", "advisory"], index=["off", "shadow", "advisory"].index(str(gem.get("mode", "shadow"))) if str(gem.get("mode", "shadow")) in ["off", "shadow", "advisory"] else 1, format_func=lambda x: {"off": "关闭", "shadow": "正式投票模式", "advisory": "咨询模式预留"}[x])
            gem["base_url"] = st.text_input("Gemini Base URL", value=str(gem.get("base_url", "https://generativelanguage.googleapis.com")))
            gem["model"] = st.text_input("Gemini 模型名称", value=str(gem.get("model", "gemini-1.5-flash")))
            g1, g2, g3 = st.columns(3)
            gem["rate_limit_seconds"] = g1.number_input("Gemini限频秒数", min_value=10, max_value=600, value=int(gem.get("rate_limit_seconds", 60)), step=10)
            gem["timeout_seconds"] = g2.number_input("Gemini超时秒数", min_value=5, max_value=60, value=int(gem.get("timeout_seconds", 20)), step=5)
            gem["max_input_chars"] = g3.number_input("Gemini最大摘要长度", min_value=1000, max_value=20000, value=int(gem.get("max_input_chars", 6000)), step=500)
            gem["cache_enabled"] = st.checkbox("Gemini启用缓存", value=bool(gem.get("cache_enabled", True)))
            gem["show_in_committee"] = st.checkbox("Gemini参与委员会展示", value=bool(gem.get("show_in_committee", True)))
            gem["include_in_replay_stats"] = st.checkbox("Gemini参与复盘统计", value=bool(gem.get("include_in_replay_stats", True)))
            st.markdown("**数据授权边界**")
            deep_perms = dict(deep.get("permissions", {}))
            gem_perms = dict(gem.get("permissions", {}))
            deep_perms["market_summary"] = st.checkbox("DeepSeek 允许读取行情摘要", value=bool(deep_perms.get("market_summary", True)))
            deep_perms["local_strategy_summary"] = st.checkbox("DeepSeek 允许读取本地策略摘要", value=bool(deep_perms.get("local_strategy_summary", True)))
            deep_perms["committee_votes_summary"] = st.checkbox("DeepSeek 允许读取委员会投票摘要", value=bool(deep_perms.get("committee_votes_summary", True)))
            deep_perms["simulation_summary"] = st.checkbox("DeepSeek 允许读取模拟交易摘要", value=bool(deep_perms.get("simulation_summary", True)))
            gem_perms["market_summary"] = st.checkbox("Gemini 允许读取行情摘要", value=bool(gem_perms.get("market_summary", True)))
            gem_perms["chart_summary"] = st.checkbox("Gemini 允许读取图表摘要", value=bool(gem_perms.get("chart_summary", True)))
            gem_perms["chart_screenshot"] = st.checkbox("Gemini 允许读取可选图表截图", value=bool(gem_perms.get("chart_screenshot", False)))
            deep_perms["account_sensitive_info"] = False
            deep_perms["api_keys"] = False
            deep_perms["trade_execution"] = False
            gem_perms["account_sensitive_info"] = False
            gem_perms["api_keys"] = False
            gem_perms["trade_execution"] = False
            deep["permissions"] = deep_perms
            gem["permissions"] = gem_perms
            updated["deepseek"] = deep
            updated["gemini"] = gem
            if st.form_submit_button("保存外部 AI 脱敏配置", use_container_width=True):
                save_external_ai_settings(updated)
                st.success("外部 AI 脱敏配置已保存。API Key 可在上方安全输入板保存或清除。")
                st.rerun()
        c1, c2 = st.columns(2)
        if c1.button("测试 DeepSeek 接入口", use_container_width=True):
            result = test_external_ai_connection("deepseek")
            (st.success if result.get("ok") else st.warning)(result.get("message"))
        if c2.button("测试 Gemini 接入口", use_container_width=True):
            result = test_external_ai_connection("gemini")
            (st.success if result.get("ok") else st.warning)(result.get("message"))
    with tabs[2]:
        st.info("账户登录 API、云端账户同步 API、多设备同步和用户配置备份接口已预留。本版本不接入外部身份系统。")
    with tabs[3]:
        st.info("Telegram、邮件、Webhook、服务器告警等通知接口已预留。本版本不发送外部通知。")
    with tabs[4]:
        audit = load_external_ai_audit_log(50)
        overrides = load_manual_position_override_log(50)
        st.markdown("**外部 AI 审计日志**")
        if not audit:
            st.caption("暂无外部 AI 调用日志。")
        for row in audit[:20]:
            st.caption(f"{row.get('time')}｜{row.get('ai_name')}｜{row.get('event')}｜{row.get('symbol')}｜敏感数据：{row.get('contains_sensitive_data')}｜{row.get('result')}")
        st.markdown("**人工仓位干预日志**")
        if not overrides:
            st.caption("暂无人工仓位干预记录。")
        for row in overrides[:20]:
            st.caption(f"{row.get('time')}｜{row.get('symbol')}｜系统{row.get('system_position_suggestion')}｜用户{row.get('user_selected_position')}%｜{row.get('result')}｜{row.get('reason')}")


def render_account_sync_center() -> None:
    """用户账户系统 + 云端同步基础中心。"""
    status = get_account_status()
    sync_status = get_cloud_sync_status()
    current_user = st.session_state.get("current_user") or {}
    current_device = st.session_state.get("current_device") or {}
    st.markdown(
        """
        <div class="app-shell">
          <div class="module-card warning-box">
            <b>账户与云同步基础中心</b><br>
            当前为单用户优先、管理员优先的基础账户系统。云同步使用本地 mock 目录，不上传 API Secret、完整 API Key 或明文密码，也不能触发交易。
          </div>
        </div>
        """,
        unsafe_allow_html=True,
    )
    render_metric_grid(
        [
            ("账户登录", "已开启" if status.get("enabled") else "默认关闭", "green" if status.get("enabled") else "yellow"),
            ("用户数量", str(status.get("user_count", 0)), "blue"),
            ("管理员", "已创建" if status.get("has_admin") else "未创建", "green" if status.get("has_admin") else "red"),
            ("活跃会话", str(status.get("active_sessions", 0)), "yellow"),
            ("当前用户", str(current_user.get("username", "未登录/未启用")), "yellow"),
            ("当前设备", str(current_device.get("device_name", "当前设备")), "blue"),
            ("同步状态", "开启" if sync_status.get("enabled") else "关闭", "yellow"),
            ("最近同步", str(sync_status.get("last_sync_time") or "暂无"), ""),
        ]
    )
    tabs = st.tabs(["账户", "设备绑定", "云同步", "同步审计", "账户审计"])

    with tabs[0]:
        if not status.get("has_admin"):
            st.markdown("**创建管理员账户**")
            with st.form("profile_admin_create_form"):
                username = st.text_input("用户名", value="admin")
                display = st.text_input("显示名称", value="管理员")
                password = st.text_input("密码", type="password")
                confirm = st.text_input("确认密码", type="password")
                if st.form_submit_button("创建 admin 管理员", use_container_width=True):
                    if password != confirm:
                        st.error("两次密码不一致。")
                    else:
                        result = create_admin_user(username, password, display)
                        (st.success if result.get("ok") else st.error)(result.get("message"))
                        st.rerun()
        else:
            st.markdown("**用户列表**")
            for user in status.get("users", []):
                st.markdown(f"{user.get('username')}｜{user.get('display_name')}｜{user.get('role')}｜{user.get('status')}｜最近登录：{user.get('last_login_time') or '暂无'}")
        if current_user:
            st.markdown("**当前用户配置**")
            profile = get_user_profile(str(current_user.get("user_id", "")))
            with st.form("user_profile_form"):
                profile["display_name"] = st.text_input("显示名称", value=str(profile.get("display_name") or current_user.get("display_name") or "管理员"))
                profile["default_page"] = st.selectbox("默认页面", list(PAGE_TITLES.keys()), index=list(PAGE_TITLES.keys()).index(str(profile.get("default_page", "home"))) if str(profile.get("default_page", "home")) in PAGE_TITLES else 0)
                profile["mobile_layout"] = st.selectbox("手机端布局", ["compact", "comfortable"], index=0 if profile.get("mobile_layout", "compact") == "compact" else 1, format_func=lambda x: "紧凑" if x == "compact" else "舒适")
                profile["show_advanced"] = st.checkbox("显示高级功能", value=bool(profile.get("show_advanced", True)))
                if st.form_submit_button("保存用户配置", use_container_width=True):
                    result = save_user_profile(str(current_user.get("user_id")), profile)
                    st.success(result.get("message"))
            with st.expander("修改密码", expanded=False):
                old_pwd = st.text_input("旧密码", type="password")
                new_pwd = st.text_input("新密码", type="password")
                new_pwd2 = st.text_input("确认新密码", type="password")
                if st.button("修改密码", use_container_width=True):
                    if new_pwd != new_pwd2:
                        st.error("两次新密码不一致。")
                    else:
                        result = change_password(str(current_user.get("username")), old_pwd, new_pwd)
                        (st.success if result.get("ok") else st.error)(result.get("message"))
            if st.button("退出当前账户会话", disabled=not bool(st.session_state.get("account_session_id")), use_container_width=True):
                result = expire_session(str(st.session_state.get("account_session_id")))
                st.session_state.pop("account_session_id", None)
                st.session_state.pop("current_user", None)
                st.success(result.get("message"))
                st.rerun()
        st.info("如需启用账户登录，请在 .env 设置 ENABLE_ACCOUNT_LOGIN=true。默认关闭以避免影响本地开发。")

    with tabs[1]:
        st.markdown("**设备绑定**")
        if current_user:
            permissions = get_user_permissions(str(current_user.get("user_id")))
            st.caption(f"当前用户权限：{', '.join(permissions[:12])}{' ...' if len(permissions) > 12 else ''}")
            if st.button("绑定当前设备到当前用户", use_container_width=True):
                result = bind_device_to_user(str(current_user.get("user_id")), str(current_device.get("device_id", "")))
                (st.success if result.get("ok") else st.error)(result.get("message"))
                st.rerun()
        else:
            st.warning("当前未登录账户。账户系统关闭时，设备仍按 8.2 session 级识别。")
        for device in load_registered_devices():
            st.markdown(
                f"""
                <div class="status-card">
                  <b>{escape(str(device.get("device_name", "-")))}</b>｜{escape(str(device.get("device_id", "-")))}<br>
                  权限：{escape(str(device.get("permission_level", "admin")))}｜可信：{"是" if device.get("trusted") else "否"}｜远控：{"允许" if device.get("allow_remote_control") else "禁止"}<br>
                  最近访问：{escape(str(device.get("last_seen_time", "-")))}｜页面：{escape(str(device.get("last_page", "-")))}
                </div>
                """,
                unsafe_allow_html=True,
            )

    with tabs[2]:
        st.markdown("**本地 mock 云同步**")
        st.caption(f"同步目录：`{sync_status.get('cloud_dir')}`")
        enabled = st.checkbox("启用同步状态标记（不自动执行）", value=bool(sync_status.get("enabled")))
        if st.button("保存同步状态", use_container_width=True):
            save_sync_status({"enabled": enabled})
            st.success("同步状态已保存。")
            st.rerun()
        c1, c2, c3 = st.columns(3)
        if c1.button("同步配置", use_container_width=True):
            result = sync_config_to_cloud()
            (st.success if result.get("ok") else st.error)(result.get("message"))
        if c2.button("同步通知已读状态", use_container_width=True):
            result = sync_notifications()
            (st.success if result.get("ok") else st.error)(result.get("message"))
        if c3.button("同步审批状态", use_container_width=True):
            result = sync_approvals()
            (st.success if result.get("ok") else st.error)(result.get("message"))
        r1, r2 = st.columns(2)
        if r1.button("同步报告清单", use_container_width=True):
            result = sync_reports()
            (st.success if result.get("ok") else st.error)(result.get("message"))
        if r2.button("同步备份清单", use_container_width=True):
            result = sync_backups()
            (st.success if result.get("ok") else st.error)(result.get("message"))
        pull_type = st.selectbox("读取云端资源", ["config", "notifications", "approvals", "reports", "backups", "user_profiles"])
        if st.button("读取 mock 云端数据", use_container_width=True):
            st.session_state["cloud_pull_result"] = pull_data(pull_type)
        if st.session_state.get("cloud_pull_result"):
            st.json(st.session_state["cloud_pull_result"])
        st.markdown("**远程备份列表**")
        for item in list_remote_backups()[:20]:
            st.caption(f"{item.get('backup_id')}｜{item.get('size')} bytes｜{item.get('updated_time')}")

    with tabs[3]:
        st.markdown("**同步审计日志**")
        audit = load_sync_audit(100)
        if not audit:
            st.info("暂无同步审计日志。")
        for row in audit:
            st.caption(f"{row.get('time')}｜{row.get('event')}｜{row.get('resource_type')}｜{row.get('result')}｜{row.get('reason', row.get('path', ''))}")

    with tabs[4]:
        st.markdown("**登录审计日志**")
        login_audit = get_login_audit(50)
        if not login_audit:
            st.info("暂无登录审计。")
        for row in login_audit:
            st.caption(f"{row.get('time')}｜{row.get('event')}｜{row.get('username')}｜{row.get('device_id')}｜{row.get('reason', '')}")
        st.markdown("**账户安全日志**")
        for row in get_account_audit(50):
            st.caption(f"{row.get('time')}｜{row.get('event')}｜{row.get('username', row.get('user_id', ''))}｜{row.get('result', '')}｜{row.get('reason', '')}")


def _render_dashboard_dict(data: dict[str, Any], skip: set[str] | None = None) -> None:
    """把指标字典安全渲染为窄表，跳过复杂嵌套字段。"""
    skip = skip or set()
    rows = []
    for key, value in data.items():
        if key in skip or isinstance(value, (dict, list)):
            continue
        rows.append({"指标": key, "数值": value})
    if rows:
        st.dataframe(rows, use_container_width=True, hide_index=True)
    else:
        st.info("暂无可展示明细。")


def render_data_dashboard_page() -> None:
    """数据看板与经营分析报告中心。"""
    render_page_head("dashboard")
    metrics = collect_all_metrics()
    overview = metrics.get("overview") or {}
    st.markdown(
        """
        <div class="app-shell">
          <div class="module-card warning-box">
            <b>数据看板只统计，不执行交易</b><br>
            本页面用于汇总模拟、实盘、审批、委员、外部AI、风控、服务器与通知数据。样本不足时只给保守观察结论，不会自动扩大额度或修改策略。
          </div>
        </div>
        """,
        unsafe_allow_html=True,
    )
    render_metric_grid(
        [
            ("系统运行天数", f"{float(overview.get('system_runtime_days', 0) or 0):.1f}", ""),
            ("自动模拟交易", str(overview.get("simulation_trades", 0)), ""),
            ("模拟总收益", f"{float(overview.get('simulation_pnl', 0) or 0):+.2f} USDT", "green" if float(overview.get("simulation_pnl", 0) or 0) >= 0 else "red"),
            ("模拟最大回撤", f"{float(overview.get('simulation_max_drawdown', 0) or 0):.2f}%", "yellow"),
            ("实盘订单", str(overview.get("live_orders", 0)), ""),
            ("实盘总盈亏", f"{float(overview.get('live_pnl', 0) or 0):+.2f} USDT", "green" if float(overview.get("live_pnl", 0) or 0) >= 0 else "red"),
            ("审批单", str(overview.get("approvals", 0)), ""),
            ("风控拦截", str(overview.get("risk_blocks", 0)), "yellow"),
            ("熔断次数", str(overview.get("circuit_breakers", 0)), "red" if int(overview.get("circuit_breakers", 0) or 0) else ""),
            ("外部AI样本", str(overview.get("deepseek_gemini_samples", 0)), ""),
            ("样本是否足够", "是" if overview.get("sample_enough") else "否", "green" if overview.get("sample_enough") else "yellow"),
            ("当前建议", str(overview.get("recommendation", "继续观察")), "yellow"),
        ]
    )

    tabs = st.tabs(["总览", "模拟交易", "实盘交易", "自动实盘", "审批流", "委员表现", "外部AI", "策略", "风控", "服务器", "通知远控", "报告", "数据质量"])
    with tabs[0]:
        st.markdown("**平台数据总览**")
        st.info(str(overview.get("recommendation", "继续观察，不建议扩大额度")))
        _render_dashboard_dict(overview)

    with tabs[1]:
        sim = metrics.get("simulation") or {}
        st.markdown("**模拟交易看板**")
        st.info(str(sim.get("sample_warning", "模拟交易样本不足，统计结果仅供参考。")))
        render_metric_grid(
            [
                ("交易次数", str(sim.get("trade_count", 0)), ""),
                ("胜率", f"{float(sim.get('win_rate', 0) or 0):.1f}%", "green"),
                ("总盈亏", f"{float(sim.get('total_pnl', 0) or 0):+.2f}", "green" if float(sim.get("total_pnl", 0) or 0) >= 0 else "red"),
                ("Profit Factor", f"{float(sim.get('profit_factor', 0) or 0):.2f}", ""),
                ("最大回撤", f"{float(sim.get('max_drawdown', 0) or 0):.2f}%", "yellow"),
                ("最佳交易", f"{float(sim.get('best_trade', 0) or 0):+.2f}", "green"),
                ("最差交易", f"{float(sim.get('worst_trade', 0) or 0):+.2f}", "red"),
                ("常见币种", str(sim.get("most_common_symbol", "-")), ""),
            ]
        )
        st.caption(f"数据源：{sim.get('source')}｜权益曲线：{sim.get('equity_source')}")
        _render_dashboard_dict(sim, {"sample_warning"})

    with tabs[2]:
        live = metrics.get("live") or {}
        st.markdown("**实盘交易看板**")
        st.warning("实盘样本较少时，不建议据此扩大资金。")
        st.info(str(live.get("sample_warning", "实盘样本不足。")))
        render_metric_grid(
            [
                ("实盘订单数", str(live.get("trade_count", 0)), ""),
                ("成交数", str(live.get("filled_count", 0)), "green"),
                ("撤单数", str(live.get("cancelled_count", 0)), "yellow"),
                ("总投入", f"{float(live.get('total_invested', 0) or 0):.2f} USDT", ""),
                ("综合盈亏", f"{float(live.get('combined_pnl', 0) or 0):+.2f} USDT", "green" if float(live.get("combined_pnl", 0) or 0) >= 0 else "red"),
                ("手续费估算", f"{float(live.get('estimated_fee', 0) or 0):.2f}", ""),
                ("人工干预", str(live.get("manual_override_count", 0)), "yellow"),
                ("胜率", f"{float(live.get('win_rate', 0) or 0):.1f}%", ""),
            ]
        )
        st.caption(f"数据源：{live.get('source')}｜持仓源：{live.get('position_source')}")
        _render_dashboard_dict(live, {"sample_warning"})

    with tabs[3]:
        auto_live = metrics.get("auto_live") or {}
        st.markdown("**自动实盘试运行看板**")
        st.info(str(auto_live.get("sample_warning", "自动实盘样本不足。")))
        render_metric_grid(
            [
                ("事件数", str(auto_live.get("event_count", 0)), ""),
                ("开启次数", str(auto_live.get("enabled_count", 0)), ""),
                ("成功数", str(auto_live.get("success_count", 0)), "green"),
                ("失败数", str(auto_live.get("failure_count", 0)), "red"),
                ("熔断次数", str(auto_live.get("circuit_breaker_count", 0)), "red"),
                ("冷却次数", str(auto_live.get("cooldown_count", 0)), "yellow"),
            ]
        )
        st.warning(str(auto_live.get("recommendation", "继续关闭自动实盘")))
        _render_dashboard_dict(auto_live, {"sample_warning"})

    with tabs[4]:
        approval = metrics.get("approval") or {}
        st.markdown("**审批流表现看板**")
        st.info(str(approval.get("sample_warning", "审批流样本不足。")))
        render_metric_grid(
            [
                ("审批总数", str(approval.get("total", 0)), ""),
                ("待审批", str(approval.get("pending", 0)), "yellow"),
                ("已批准", str(approval.get("approved", 0)), "green"),
                ("已拒绝", str(approval.get("rejected", 0)), "red"),
                ("已修改", str(approval.get("modified", 0)), ""),
                ("已过期", str(approval.get("expired", 0)), "yellow"),
                ("执行成功", str(approval.get("executed", 0)), "green"),
                ("执行失败", str(approval.get("failed", 0)), "red"),
            ]
        )
        _render_dashboard_dict(approval, {"sample_warning"})

    with tabs[5]:
        committee = metrics.get("committee") or {}
        st.markdown("**委员表现看板**")
        st.info(str(committee.get("sample_warning", "委员投票样本不足，暂不建议调整权重。")))
        members = committee.get("members") or {}
        if not members:
            st.info("暂无委员投票数据。")
        else:
            st.dataframe([{"委员": name, **value} for name, value in members.items()], use_container_width=True, hide_index=True)
        st.caption(f"数据源：{committee.get('source')}")

    with tabs[6]:
        external_ai = metrics.get("external_ai") or {}
        st.markdown("**DeepSeek / Gemini 表现看板**")
        st.info(str(external_ai.get("sample_warning", "外部AI样本不足。")))
        st.warning(str(external_ai.get("upgrade_suggestion", "外部AI样本不足，暂不建议提高权重。")))
        ai_stats = external_ai.get("ai_stats") or {}
        if not ai_stats:
            st.info("暂无外部AI审计数据。")
        else:
            st.dataframe([{"AI": name, **value} for name, value in ai_stats.items()], use_container_width=True, hide_index=True)
        st.caption(f"数据源：{external_ai.get('source')}")

    with tabs[7]:
        strategy = metrics.get("strategy") or {}
        st.markdown("**策略表现看板**")
        st.info(str(strategy.get("sample_warning", "策略样本不足。")))
        render_metric_grid([("策略样本", str(strategy.get("strategy_count", 0)), ""), ("数据质量", str(strategy.get("data_quality", "poor")), "yellow")])
        st.markdown("评级分布")
        st.json(strategy.get("grade_summary", {}))
        st.markdown("过拟合风险分布")
        st.json(strategy.get("overfit_summary", {}))
        st.caption(f"数据源：{strategy.get('source')}")

    with tabs[8]:
        risk = metrics.get("risk") or {}
        st.markdown("**风控表现看板**")
        st.info(str(risk.get("conclusion", "风控样本不足，建议维持保守设置。")))
        render_metric_grid(
            [
                ("风险事件", str(risk.get("risk_event_count", 0)), ""),
                ("否决次数", str(risk.get("risk_veto_count", 0)), "red"),
                ("数据质量拦截", str(risk.get("data_quality_block_count", 0)), "yellow"),
                ("API异常拦截", str(risk.get("api_error_block_count", 0)), "red"),
                ("熔断次数", str(risk.get("circuit_breaker_count", 0)), "red"),
                ("冷却次数", str(risk.get("cooldown_count", 0)), "yellow"),
            ]
        )
        st.caption(f"数据源：{risk.get('source')}")

    with tabs[9]:
        server = metrics.get("server") or {}
        st.markdown("**服务器运行看板**")
        st.info(str(server.get("stability", "暂无服务器运行日志。")))
        render_metric_grid(
            [
                ("运行事件", str(server.get("runtime_events", 0)), ""),
                ("估算运行天数", f"{float(server.get('estimated_runtime_days', 0) or 0):.1f}", ""),
                ("错误数", str(server.get("error_count", 0)), "red" if int(server.get("error_count", 0) or 0) else ""),
                ("重启数", str(server.get("restart_count", 0)), "yellow"),
            ]
        )
        st.caption(f"数据源：{server.get('source')}")

    with tabs[10]:
        notification = metrics.get("notification") or {}
        st.markdown("**通知与远程操作看板**")
        render_metric_grid(
            [
                ("通知总数", str(notification.get("notification_count", 0)), ""),
                ("未读通知", str(notification.get("unread_count", 0)), "yellow"),
                ("紧急通知", str(notification.get("urgent_count", 0)), "red"),
                ("审批通知", str(notification.get("approval_notice_count", 0)), ""),
                ("风险通知", str(notification.get("risk_notice_count", 0)), "yellow"),
                ("远程操作", str(notification.get("remote_action_count", 0)), ""),
                ("登录失败", str(notification.get("login_failure_count", 0)), "red"),
            ]
        )
        st.caption(f"数据源：{notification.get('source')}")

    with tabs[11]:
        st.markdown("**日报 / 周报 / 月报生成器**")
        c1, c2, c3 = st.columns(3)
        generated_report = None
        if c1.button("生成今日报告", use_container_width=True):
            generated_report = generate_daily_report()
        if c2.button("生成本周报告", use_container_width=True):
            generated_report = generate_weekly_report()
        if c3.button("生成本月报告", use_container_width=True):
            generated_report = generate_monthly_report()
        if generated_report:
            st.success("报告已生成。")
            st.json(generated_report.get("files", {}))
            st.download_button("下载 Markdown", export_report_markdown(generated_report), file_name=f"{generated_report.get('kind')}_report.md", mime="text/markdown", use_container_width=True)
            st.download_button("下载 JSON", export_report_json(generated_report), file_name=f"{generated_report.get('kind')}_report.json", mime="application/json", use_container_width=True)
            st.download_button("下载 CSV 指标摘要", export_report_csv(generated_report), file_name=f"{generated_report.get('kind')}_metrics.csv", mime="text/csv", use_container_width=True)
        reports = load_recent_reports()
        if reports:
            st.dataframe(reports, use_container_width=True, hide_index=True)
        else:
            st.info("暂无已生成报告。")

    with tabs[12]:
        quality = metrics.get("quality") or {}
        render_cognition_snapshot_validation_panel()
        st.markdown("**数据质量检查**")
        if quality.get("checks"):
            st.dataframe(quality.get("checks"), use_container_width=True, hide_index=True)
        issues = quality.get("issues") or []
        if issues:
            st.warning(f"检测到 {quality.get('issue_count', 0)} 条历史数据质量记录，已跳过异常数据。")
            st.dataframe(issues, use_container_width=True, hide_index=True)
        else:
            st.info("暂无数据质量异常记录。")


def render_profile(symbols: list[str], snapshot: dict[str, Any]) -> None:
    """我的页：集中放置调试、搜索和刷新控制。"""
    render_page_head("profile")
    render_symbol_search_panel(symbols, "profile")
    render_api_external_interface_center()
    render_account_sync_center()
    st.button("刷新行情", on_click=refresh_all_now, use_container_width=True)
    st.markdown(
        f"""<div class="app-shell"><div class="status-card">
        <b>系统状态中心</b><br>
        Binance连接状态：{snapshot.get("binance_status", "初始化")}<br>
        当前交易对象：{snapshot.get("current_symbol", "-")}<br>
        数据源：Binance Public REST API<br>
        K线数据源：Binance Public REST Kline<br>
        当前版本：{VERSION}<br>
        行情刷新频率：1秒<br>
        K线刷新频率：1秒<br>
        榜单完整扫描频率：3秒<br>
        衍生品刷新频率：30秒<br>
        大单刷新频率：5秒<br>
        最后行情更新时间：{snapshot.get("last_update_time", "初始化中")}<br>
        最后K线更新时间：{snapshot.get("kline_last_update_time", "初始化中")}<br>
        最后盘口更新时间：{snapshot.get("orderbook_last_update_time", "初始化中")}<br>
        最后衍生品更新时间：{snapshot.get("derivatives_last_update_time", "初始化中")}<br>
        最后大单更新时间：{snapshot.get("whale_last_update_time", "初始化中")}<br>
        大单状态：{snapshot.get("whale_status", "初始化")}<br>
        当前K线周期：{snapshot.get("kline_interval", "1m")}<br>
        交易对象数量：{len(symbols)}<br>
        最近错误信息：{snapshot.get("last_error") or "无"}<br>
        K线错误信息：{snapshot.get("kline_last_error") or "无"}<br>
        盘口错误信息：{snapshot.get("orderbook_last_error") or "无"}<br>
        衍生品错误信息：{snapshot.get("derivatives_last_error") or "无"}<br>
        大单错误信息：{snapshot.get("whale_last_error") or "无"}
        </div></div>""",
        unsafe_allow_html=True,
    )
    current = escape(str(st.session_state.get("current_symbol", "BTCUSDT")))
    st.markdown(
        f"""<div class="app-shell"><div class="module-grid">
          <div class="module-card"><div class="module-title">API状态</div><div class="status-card">Binance：{escape(str(snapshot.get("binance_status", "初始化")))}｜DeepSeek/Gemini 状态见上方外部AI接口中心。</div></div>
          <div class="module-card"><div class="module-title">数据源状态</div><div class="status-card">Ticker：{escape(str(snapshot.get("last_update_time", "初始化中")))}｜K线：{escape(str(snapshot.get("kline_last_update_time", "初始化中")))}｜盘口：{escape(str(snapshot.get("orderbook_last_update_time", "初始化中")))}</div></div>
          <div class="module-card"><div class="module-title">版本信息</div><div class="status-card">{escape(VERSION)}｜交易对象：{current}</div></div>
          <div class="module-card"><div class="module-title">系统日志</div><div class="status-card"><a class="watch-pill" href="?page=server&symbol={current}" target="_self">查看系统运维中心</a></div></div>
          <div class="module-card"><div class="module-title">策略工厂</div><div class="status-card"><a class="watch-pill" href="?page=learning&symbol={current}" target="_self">查看复盘与策略数据</a></div></div>
          <div class="module-card"><div class="module-title">安全锁</div><div class="status-card"><a class="watch-pill" href="?page=live&symbol={current}" target="_self">查看实盘安全中心</a></div></div>
        </div></div>""",
        unsafe_allow_html=True,
    )


def render_error(snapshot: dict[str, Any]) -> None:
    """仅在有错误时显示，不占用正常页面空间。"""
    if snapshot.get("last_error"):
        st.markdown(f'<div class="app-shell"><div class="error-box">{snapshot["last_error"]}</div></div>', unsafe_allow_html=True)


def render_page(page_key: str, symbol: str, ticker: dict[str, Any] | None, rankings: dict[str, list[dict[str, Any]]] | None, snapshot: dict[str, Any], symbols: list[str], scores: dict[str, Any]) -> None:
    """根据当前页面渲染独立内容。"""
    if page_key == "home":
        render_home(ticker, snapshot, scores, symbols, rankings)
    elif page_key == "market":
        render_market_realtime(rankings)
    elif page_key == "signals":
        render_signals(symbol, ticker, scores)
    elif page_key == "trading":
        render_trading()
    elif page_key == "trade_records":
        render_trade_records_page()
    elif page_key == "positions":
        render_positions()
    elif page_key == "learning":
        render_learning()
    elif page_key == "strategy":
        render_strategy_factory()
    elif page_key == "live":
        render_live_trading_center()
    elif page_key in {"approval", "auto_trade"}:
        render_approval_center_page()
    elif page_key == "dashboard":
        render_data_dashboard_page()
    elif page_key == "remote":
        render_remote_control_page()
    elif page_key == "server":
        render_server_health_page()
    elif page_key == "profile":
        render_profile(symbols, snapshot)


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
    if st.button("进入系统", use_container_width=True):
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
            if st.form_submit_button("创建管理员", use_container_width=True):
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
        if st.form_submit_button("登录", use_container_width=True):
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
    init_state()
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
    start_local_api_server()
    start_background_refresher()
    inject_styles()
    render_fixed_market_bar(current_symbol)
    render_error(snapshot)
    render_bootstrap_status()
    if bootstrap_result.get("ok") and not st.session_state.get("initial_load_done"):
        st.session_state["initial_load_done"] = True
        st.rerun()
    render_page(st.session_state.active_page, current_symbol, ticker, rankings, snapshot, symbols, scores)
    render_bottom_nav()


if __name__ == "__main__":
    main()
