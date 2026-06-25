"""Session state and current-symbol coordination."""

from __future__ import annotations

import time
from typing import Any

import streamlit as st

from services import market_cache
from services.background_refresher import refresh_klines_now, refresh_orderbook_now, refresh_symbol_now, refresh_whales_now
from services.remote_control_center import get_current_device_info, register_device, update_device_last_seen
from services.watchlist_manager import add_to_watchlist


def initialize_session_state() -> None:
    """Initialize first-screen state without overriding existing user choices."""
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


def init_state(page_titles: dict[str, tuple[str, str]], ma_options: list[str]) -> None:
    """Initialize page state without directly requesting Binance."""
    initialize_session_state()
    page = st.query_params.get("page", "home")
    st.session_state.active_page = page if page in page_titles else "home"
    query_symbol = str(st.query_params.get("symbol", "") or "").upper().strip()
    grid_view = str(st.query_params.get("grid_view", "") or "").strip().lower() in {"1", "true", "yes", "on"}
    watch_add_symbol = str(st.query_params.get("watch_add", "") or "").upper().strip()
    initial_symbol = query_symbol or market_cache.get_current_symbol()
    st.session_state.setdefault("current_symbol", initial_symbol)
    st.session_state.setdefault("selected_symbol", st.session_state.current_symbol)
    st.session_state.setdefault("symbol_search", "")
    st.session_state.setdefault("kline_interval", market_cache.get_kline_interval())
    st.session_state.setdefault("ma_visibility", ma_options)
    st.session_state.setdefault("follow_latest", True)
    st.session_state.setdefault("chart_interactive", False)
    st.session_state.setdefault("watchlist", [])
    if query_symbol and query_symbol != st.session_state.current_symbol:
        set_current_symbol(query_symbol, source="grid_temp_view" if grid_view else "url_param")
    elif query_symbol and grid_view:
        st.session_state["current_symbol_source"] = "grid_temp_view"
    if watch_add_symbol:
        add_to_watchlist(watch_add_symbol, source="市场榜单", category="ai")
    market_cache.set_current_symbol(st.session_state.current_symbol)
    market_cache.set_kline_interval(st.session_state.kline_interval)


def ensure_current_device() -> dict[str, Any]:
    """Register the current Streamlit session as a remote-control device."""
    st.session_state.setdefault("device_id", f"dev_{id(st.session_state):x}")
    st.session_state.setdefault("device_name", "当前设备")
    page = st.session_state.get("active_page", "home")
    info = get_current_device_info(
        str(st.session_state.get("device_id")),
        "",
        page,
        str(st.session_state.get("device_name", "当前设备")),
    )
    device = register_device(info)
    update_device_last_seen(str(device.get("device_id", "")), page)
    st.session_state["current_device"] = device
    return device


def on_symbol_change() -> None:
    """Shared symbol switch callback."""
    set_current_symbol(st.session_state.selected_symbol, source="manual_select")


def is_user_selected_symbol_source(source: Any | None = None) -> bool:
    """Return whether current symbol came from an explicit user action."""
    value = str(source if source is not None else st.session_state.get("current_symbol_source", "")).strip()
    if not value:
        return False
    return (
        value in {"manual_select", "url_param", "opportunity_board_click"}
        or value.endswith("_search")
        or value.startswith("watch_")
    )


def set_current_symbol(symbol: str, source: str = "manual_select") -> None:
    """Write the single global current symbol and synchronize dependent panels."""
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


def refresh_all_now() -> None:
    """Request a refresh for the current symbol."""
    market_cache.set_current_symbol(st.session_state.current_symbol)
    market_cache.request_refresh()
