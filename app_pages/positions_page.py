"""Positions and order overview page."""

from __future__ import annotations

from html import escape
from pathlib import Path
from typing import Any

import streamlit as st

from components.ui import kline_symbol_link, render_kline_jump_links, render_metric_grid, render_page_head
from services import market_cache
from services.background_refresher import refresh_symbol_now
from services.live_trading_center import load_live_order_records
from services.sim_trade_engine import get_sim_account_summary, update_simulation
from utils.formatters import format_price, format_waiting_price, safe_number

LOG_DIR = Path(__file__).resolve().parents[1] / "logs"
POSITION_PRICE_LOG = LOG_DIR / "position_price_debug.log"


def append_debug_log(path: Path, message: str) -> None:
    try:
        LOG_DIR.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as handle:
            handle.write(f"{__import__('time').strftime('%Y-%m-%d %H:%M:%S')} {message}\n")
    except Exception:
        pass


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


def render_positions(page_titles: dict[str, tuple[str, str]], version: str, current_symbol: str = "BTCUSDT") -> None:
    """持仓页。"""
    render_page_head("positions", page_titles, version)
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
              <a class="watch-pill" href="?page=trading&symbol={escape(str(current_symbol))}" target="_self">进入交易页</a>
              <a class="watch-pill" href="?page=trade_records&symbol={escape(str(current_symbol))}" target="_self">交易记录</a>
              {kline_symbol_link(current_symbol, "当前币种K线", "watch-pill")}
            </div>
          </div>
        </div></div>
        """,
        unsafe_allow_html=True,
    )
